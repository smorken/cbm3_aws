import uuid
import datetime
from types import SimpleNamespace
from cbm3_aws import constants


def create_userdata():
    """creates the script to run at the start of each instance worker

    Returns:
        str: lines of commands to run in AWS EC2 user-data at EC2 startup
    """
    commands = [
        "<script>",
        "shutdown /s",
        "</script>"
    ]
    return "\n".join(commands)


def delete_launch_template(client, context):
    """Drop launch template associated with the specified context

    Args:
        client (EC2.Client): boto3 ec2 client
        context (object): context object returned by:
            :py:func:`create_launch_template`
    """
    client.delete_launch_template(
        DryRun=False,
        LaunchTemplateId=context.launch_template_id)


def create_launch_template(client, image_ami_id, instance_type,
                           iam_instance_profile_arn, user_data):
    """Create a launch template for provisioning instances

    Args:
        client (EC2.Client): boto3 ec2 client
        image_ami_id (str): the ami id for the launched instances
        instance_type (str): the type of the instance to launch
            (ex. 't1.micro')
        iam_instance_profile_arn (str): ARN for for the Iam instance profile
            to attach to launched instances
        user_data (str): line break seperated commands to run on instance start

    Returns:
        object: launch template context object
    """
    client_token = str(uuid.uuid4())
    spot_request_valid_date = \
        datetime.datetime.today() + datetime.timedelta(days=7)
    response = client.create_launch_template(
        DryRun=False,
        ClientToken=client_token,
        LaunchTemplateName=constants.AUTOSCALE_LAUNCH_TEMPLATE_NAME,
        LaunchTemplateData={
            'EbsOptimized': False,
            'IamInstanceProfile': {
                'Arn': iam_instance_profile_arn,
            },
            'ImageId': image_ami_id,
            'InstanceType': instance_type,
            'Monitoring': {
                'Enabled': True
            },
            'InstanceInitiatedShutdownBehavior': 'terminate',
            'UserData': create_userdata(),
            'TagSpecifications': [
                {
                    'ResourceType': 'instance',
                    'Tags': [
                        {
                            'Key': 'Name',
                            'Value': 'CBM3 Worker Instance'
                        },
                    ]
                },
                {
                    'ResourceType': 'volume',
                    'Tags': [
                        {
                            'Key': 'Name',
                            'Value': 'CBM3 Worker volume'
                        },
                    ]
                },
            ],
            'InstanceMarketOptions': {
                'MarketType': 'spot',
                'SpotOptions': {
                    # 'MaxPrice': 'string', # up to the default on-demand price
                    'SpotInstanceType': 'one-time',
                    'ValidUntil': spot_request_valid_date,
                    'InstanceInterruptionBehavior': 'terminate'
                }
            }
        },
        TagSpecifications=[
            {
                'ResourceType': 'launch-template',
                'Tags': [
                    {
                        'Key': 'name',
                        'Value': 'CBM3 launch template'
                    },
                ]
            },
        ]
    )

    return SimpleNamespace(
        launch_template_name=response["LaunchTemplateName"],
        launch_template_id=response["LaunchTemplateId"])


def create_autoscaling_group(client, launch_template_context, size):
    """Create an autoscaling group to manage spot instances.

    Args:
        client (AutoScaling.Client): boto3 autoscaling client
        launch_template_context (object): Return value of:
            :py:func:`create_launch_template`
        size (int): number of instances to run in auto scaling group

    Returns:
        object: autoscaling group context
    """
    response = client.create_auto_scaling_group(
        AutoScalingGroupName=constants.AUTOSCALE_GROUP_NAME,
        LaunchTemplate={
            'LaunchTemplateId': launch_template_context.launch_template_id,
        },
        MinSize=size,
        MaxSize=size,
        NewInstancesProtectedFromScaleIn=True,
    )
    return SimpleNamespace(
        auto_scaling_group_name=response["AutoScalingGroupName"])


def delete_autoscaling_group(client, context):
    """Delete an autoscaling group created by
        :py:func:`create_autoscaling_group`

    Args:
        client (AutoScaling.Client): boto3 autoscaling client
        context (object): context object returned by:
            :py:func:`create_autoscaling_group`
    """
    client.delete_auto_scaling_group(
        AutoScalingGroupName=context.auto_scaling_group_name,
        ForceDelete=True)