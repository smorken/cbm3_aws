import os
import json
import boto3
from botocore.exceptions import ClientError

from cbm3_aws.instance.user_data import create_userdata
from cbm3_aws.aws import roles
from cbm3_aws.namespace import Namespace
from cbm3_aws.aws import step_functions
from cbm3_aws.aws import autoscale_group
from cbm3_aws.aws import s3_bucket
from cbm3_aws.aws.names import get_names
from cbm3_aws.aws.names import get_uuid
from cbm3_aws import log_helper


def __s3_bucket_exists(client, bucket_name):
    bucket = client.Bucket(bucket_name)

    if bucket.creation_date:
        return True
    else:
        return False


def __get_account_number(sts_client):
    return sts_client.get_caller_identity()["Account"]


def __write_resources_file(resource_description, out_dir, uuid):
    path = os.path.join(out_dir, f"aws_resources_{uuid}.json")
    with open(path, 'w') as out_file:
        json.dump(resource_description.to_dict(), out_file, indent=4)


def deploy(region_name, s3_bucket_name, min_instances, max_instances,
           image_ami_id, instance_type, resource_description_out_dir):
    logger = log_helper.get_logger()

    # resource description
    rd = Namespace()
    rd.uuid = get_uuid()
    __write_resources_file(rd, resource_description_out_dir, rd.uuid)
    rd.names = get_names(rd.uuid)
    rd.region_name = region_name
    rd.s3_bucket_name = s3_bucket_name
    rd.min_instances = min_instances
    rd.max_instances = max_instances
    rd.image_ami_id = image_ami_id
    rd.instance_type = instance_type

    try:

        s3_client = boto3.client("s3", region_name=rd.region_name)
        ec2_client = boto3.client("ec2")  # region_name=region_name)
        auto_scale_client = boto3.client(
            'autoscaling', region_name=rd.region_name)
        iam_client = boto3.client("iam", region_name=rd.region_name)
        sts_client = boto3.client("sts", region_name=rd.region_name)
        sfn_client = boto3.client('stepfunctions', region_name=rd.region_name)

        if not __s3_bucket_exists(s3_client, rd.s3_bucket_name):
            logger.info(f"creating s3 bucked {rd.s3_bucket_name}")
            s3_bucket.create_bucket(
                client=s3_client, bucket_name=rd.s3_bucket_name,
                region=rd.region_name)

        account_number = __get_account_number(sts_client)
        logger.info("creating policies")
        rd.s3_bucket_policy_context = roles.create_s3_bucket_policy(
            client=iam_client, s3_bucket_name=rd.s3_bucket_name)
        rd.state_machine_policy_context = roles.create_state_machine_policy(
            client=iam_client, account_number=account_number, names=rd.names)
        autoscale_update_policy = roles.create_autoscaling_group_policy(
            client=iam_client, account_number=account_number,
            names=rd.names.autoscale_group)

        logger.info("creating iam roles")
        instance_iam_role_context = roles.create_instance_iam_role(
            client=iam_client,
            policy_context_list=[
                rd.s3_bucket_policy_context,
                rd.state_machine_policy_context,
                autoscale_update_policy])

        rd.state_machine_role_context = roles.create_state_machine_role(
            client=iam_client,
            policy_context_list=[rd.state_machine_policy_context])

        rd.state_machine_context = step_functions.create_state_machines(
            client=sfn_client, role_arn=rd.state_machine_role_context.role_arn,
            max_concurrency=rd.max_instances, names=rd.names)

        rd.user_data = create_userdata(
            s3_bucket_name=rd.s3_bucket_name,
            activity_arn=rd.state_machine_context.activity_arn)

        rd.launch_template_context = autoscale_group.create_launch_template(
            client=ec2_client, image_ami_id=rd.image_ami_id,
            instance_type=rd.instance_type,
            iam_instance_profile_arn=instance_iam_role_context.role_arn,
            user_data=rd.user_data)

        rd.autoscale_group_context = autoscale_group.create_autoscaling_group(
            client=auto_scale_client,
            launch_template_context=rd.launch_template_context,
            min_size=rd.min_instances,
            max_size=rd.max_instances)

        return rd

    except ClientError as err:
        # from:
        # https://boto3.amazonaws.com/v1/documentation/api/latest/guide/error-handling.html
        if err.response['Error']['Code'] == 'InternalError':  # Generic error
            logger.error(
                'Error Message: {}'.format(
                    err.response['Error']['Message']))
            logger.error(
                'Request ID: {}'.format(
                    err.response['ResponseMetadata']['RequestId']))
            logger.error(
                'Http code: {}'.format(
                    err.response['ResponseMetadata']['HTTPStatusCode']))
        else:
            raise err
    finally:
        __write_resources_file(rd, resource_description_out_dir, rd.uuid)
