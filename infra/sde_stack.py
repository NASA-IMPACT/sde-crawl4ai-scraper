from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
    Tags,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_s3 as s3,
)
from constructs import Construct


class SdeCrawlerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        bucket = s3.Bucket(
            self,
            "CrawlBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=False,
            removal_policy=RemovalPolicy.RETAIN,
        )

        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                )
            ],
        )

        role = iam.Role(
            self,
            "InstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMManagedInstanceCore"
                ),
            ],
        )
        bucket.grant_read_write(role)

        sg = ec2.SecurityGroup(
            self,
            "InstanceSg",
            vpc=vpc,
            description="SDE crawler (egress only)",
            allow_all_outbound=True,
        )

        instance_type = ec2.InstanceType.of(
            ec2.InstanceClass.T3, ec2.InstanceSize.XLARGE
        )

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "dnf install -y python3.11 python3.11-pip python3.11-devel git gcc inotify-tools "
            "at-spi2-atk libX11 libXcomposite libXcursor libXdamage libXext "
            "libXi libXrandr libXScrnSaver libXtst pango alsa-lib nss nspr "
            "cups-libs mesa-libgbm libdrm atk at-spi2-core libxkbcommon",
            "mkdir -p /opt/sde-crawler /etc/sde",
            f"printf 'SDE_S3_BUCKET={bucket.bucket_name}\\n' > /etc/sde/env",
            "chmod 644 /etc/sde/env",
            "chown -R ec2-user:ec2-user /opt/sde-crawler || true",
        )

        instance = ec2.Instance(
            self,
            "Crawler",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            instance_type=instance_type,
            machine_image=ec2.MachineImage.latest_amazon_linux2023(),
            role=role,
            security_group=sg,
            user_data=user_data,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(
                        50,
                        volume_type=ec2.EbsDeviceVolumeType.GP3,
                        encrypted=True,
                    ),
                )
            ],
            associate_public_ip_address=True,
        )

        Tags.of(instance).add("Name", "sde-crawler")
        Tags.of(bucket).add("Name", "sde-crawler-output")

        CfnOutput(self, "InstanceId", value=instance.instance_id)
        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(
            self,
            "ConnectHint",
            value=(
                f"aws ssm start-session --target {instance.instance_id}"
            ),
        )
