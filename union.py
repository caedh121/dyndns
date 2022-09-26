"""
Credits to   Jeremy Cowan, Efrain Fuentes and Bryan Liston from AWS
https://aws.amazon.com/blogs/compute/building-a-dynamic-dns-for-route-53-using-cloudwatch-events-and-lambda/
https://github.com/aws-samples/aws-lambda-ddns-function

DDNS Lambda Python3 Script

This script will perform the following functions.

IF:
   the instance is running in a VPC with a custom dhcp option set
    with a dns domain name that matches a privated hosted zone created in route53,
THEN:
 1. An 'A' record using the Tag "Name" value is created to the secondary IP,
    if no Tag "Name" then it uses private_DNS_name,
    if no secondary IP then it uses the primary IP
 2. IF:
        the PTR Zone doesnt exits
    THEN:
        it creates the PTR zone , associates it with the VPC
        and A 'PTR" record is create to the DNS name,

Creates a dynamodb table with all not empty properties of the instance excluding metadata

"""
import json
import sys
import datetime
import random
import logging
import re
import uuid
import time
import inspect
import boto3
from botocore.exceptions import ClientError

# Setting Global Variables
LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)
ACCOUNT = None
REGION = None
SNS_CLIENT = None

print('Loading function ' + datetime.datetime.now().time().isoformat())


def lineno():  # pragma: no cover
    """
    Returns the current line number in our script
    :return:
    """
    return str(' - line number: ' + str(inspect.currentframe().f_back.f_lineno))


def get_sns_client():
    """
    Get sns client
    :return:
    """
    try:
        return boto3.client('sns')
    except ClientError as err:
        print("Unexpected error: %s" % err)


def get_route53_client():
    """
    Get route53 client
    :return:
    """
    try:
        return boto3.client('route53')
    except ClientError as err:
        print("Unexpected error: %s" % err)


def get_ec2_client():
    """
    Get ec2 client
    :return:
    """
    try:
        return boto3.client('ec2')
    except ClientError as err:
        print("Unexpected error: %s" % err)


def get_instance_name(fid):
    # When given an instance ID as str e.g. 'i-1234567', return the instance 'Name' from the name tag.
    ec2 = boto3.resource('ec2')
    ec2instance = ec2.Instance(fid)
    instancename = ''
    for tags in ec2instance.tags:
        if tags["Key"] == 'Name':
            instancename = tags["Value"]
    return instancename


def get_dynamodb_client():
    """
    Get dynamodb client
    :return:
    """
    try:
        return boto3.client('dynamodb')
    except ClientError as err:
        print("Unexpected error: %s" % err)


def lambda_handler(
        event,
        context,
        dynamodb_client=get_dynamodb_client(),
        compute=get_ec2_client(),
        route53=get_route53_client(),
        sns_client=get_sns_client()
):
    """
    Check to see whether a DynamoDB table already exists.  If not, create it.
    This table is used to keep a record of instances that have been created
    along with their attributes.  This is necessary because when you terminate an instance
    its attributes are no longer available, so they have to be fetched from the table.
    :param event:
    :param context:
    :param dynamodb_client:
    :param compute:
    :param route53:
    :param sns_client:
    :return:
    """
    LOGGER.info("event: %s", str(event) + lineno())
    LOGGER.info("context: %s", str(context) + lineno())
    SNS_CLIENT = sns_client

    # Checking to make sure there is a dynamodb table named DDNS or we will create it
    tables = list_tables(dynamodb_client)

    LOGGER.info("tables: %s", str(tables))
    if 'DDNS' in tables['TableNames']:
        LOGGER.info('DynamoDB table already exists')
    else:
        create_table(dynamodb_client, 'DDNS')

    # Set variables
    # Get the state from the Event stream
    state = event['detail']['state']
    LOGGER.debug("instance state: %s", str(state) + lineno())

    # Get the instance id, region, and tag collection
    instance_id = event['detail']['instance-id']
    LOGGER.debug("instance id: %s", str(instance_id) + lineno())
    ACCOUNT = event['account']
    region = event['region']
    REGION = region
    LOGGER.debug("region: %s", str(region) + lineno())

    # Only doing something if the state is running
    if state == 'running':
        LOGGER.debug("sleeping for 1 milisecond %s", lineno())

        if "pytest" in sys.modules:
            # called from within a test run
            time.sleep(1/1000)
        else:
            # called "normally"
            time.sleep(1/1000)

        # Get instance information
        instance = get_instances(compute, instance_id)
        # Remove response metadata from the response
        if 'ResponseMetadata' in instance:
            instance.pop('ResponseMetadata')
        # Remove null values from the response.  You cannot save a dict/JSON
        # document in DynamoDB if it contains null values
        LOGGER.debug("instance: %s", str(instance) + lineno())
        instance = remove_empty_from_dict(instance)
        instance_dump = json.dumps(instance, default=json_serial)
        instance_attributes = json.loads(instance_dump)
        LOGGER.debug("instance_attributes: %s", str(instance_attributes) + lineno())
        LOGGER.debug("trying to put instance information in "
                     "dynamo table %s", str(instance_attributes) + lineno())
        put_item_in_dynamodb_table(dynamodb_client, 'DDNS', instance_id, instance_attributes)
        LOGGER.debug("done putting item in dynamo table %s", lineno())
    else:
        # Fetch item from DynamoDB
        LOGGER.debug("Fetching instance information from dynamodb %s", lineno())
        instance = get_item_from_dynamodb_table(dynamodb_client, 'DDNS', instance_id)
        LOGGER.debug("instance: %s", str(instance) + lineno())

    LOGGER.debug("Get instance attributes %s", lineno())
    LOGGER.debug("instance: %s", str(instance) + lineno())
    LOGGER.debug("type: %s", str(type(instance)) + lineno())
    if instance and 'Reservations' in instance:
        LOGGER.debug("reservations: %s", str(instance['Reservations']) + lineno())
        LOGGER.debug("reservations: %s", str(instance['Reservations'][0]) + lineno())
        LOGGER.debug("reservations: %s", str(instance['Reservations'][0]['Instances']) + lineno())
        LOGGER.debug("reservations:"
                     " %s", str(instance['Reservations'][0]['Instances'][0]) + lineno())

    private_dns_name = instance['Reservations'][0]['Instances'][0]['PrivateDnsName']

    try:
        private_host_name = get_instance_name(f'{instance_id}')
    except:
        private_host_name = private_dns_name.split('.')[0]

    if private_host_name == '':
        private_host_name = private_dns_name.split('.')[0]

    try:
        private_ip = instance['Reservations'][0]['Instances'][0]['NetworkInterfaces'][0]['PrivateIpAddresses'][1]['PrivateIpAddress']
    except:
        private_ip = instance['Reservations'][0]['Instances'][0]['NetworkInterfaces'][0]['PrivateIpAddresses'][0]['PrivateIpAddress']


    LOGGER.info("private ip: %s", str(private_ip) + lineno())
    LOGGER.info("private_dns_name: %s", str(private_dns_name) + lineno())
    LOGGER.info("private_host_name: %s", str(private_host_name) + lineno())

    # Get the subnet mask of the instance
    subnet_id = instance['Reservations'][0]['Instances'][0]['SubnetId']
    LOGGER.debug("subnet_id: %s", str(subnet_id) + lineno())
    cidr_block = get_subnet_cidr_block(compute, subnet_id)
    LOGGER.debug("cidr_block: %s", str(cidr_block) + lineno())
    subnet_mask = int(cidr_block.split('/')[-1])
    LOGGER.debug("subnet_mask: %s", str(subnet_mask) + lineno())
    reversed_ip_address = reverse_list(private_ip)

    reversed_domain_prefix = get_reversed_domain_prefix(subnet_mask, private_ip)
    reversed_domain_prefix = reverse_list(reversed_domain_prefix)
    LOGGER.debug("reversed_domain_prefix is: %s", str(reversed_domain_prefix) + lineno())
    # Set the reverse lookup zone
    reversed_lookup_zone = reversed_domain_prefix + 'in-addr.arpa.'
    LOGGER.info("The reverse lookup zone for this instance is: %s", str(reversed_lookup_zone))

    # Get VPC id
    vpc_id = instance['Reservations'][0]['Instances'][0]['VpcId']

    # Are DNS Hostnames and DNS Support enabled?
    if is_dns_hostnames_enabled(compute, vpc_id):
        LOGGER.debug("DNS hostnames enabled for %s", str(vpc_id) + lineno())
    else:
        LOGGER.debug("DNS hostnames disabled for %s. You have to enable DNS hostnames \
to use Route 53 private hosted zones. %s", vpc_id, lineno())
    if is_dns_support_enabled(compute, vpc_id):
        LOGGER.debug("DNS support enabled for %s", str(vpc_id) + lineno())
    else:
        LOGGER.debug("DNS support disabled for %s.  You have to enabled DNS support \
to use Route 53 private hosted zones. %s", str(vpc_id), lineno())
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, 'DNS support disabled for ' +
                       str(vpc_id) + '.  You have to enabled DNS support \
to use Route 53 private hosted zones. ' + lineno())
        exit()

    # These are collections of zones in Route 53.
    hosted_zones = list_hosted_zones(route53)
    LOGGER.debug("hosted_zones: %s", str(hosted_zones) + lineno())
    private_hosted_zones = get_private_hosted_zones(hosted_zones)
    LOGGER.debug("private_hosted_zones: %s", str(list(private_hosted_zones)) + lineno())
    private_hosted_zone_collection = get_private_hosted_zone_collection(private_hosted_zones)
    LOGGER.debug("private_hosted_zone_collection: %s",
                 str(list(private_hosted_zone_collection)) + lineno())


    # Check to see whether a reverse lookup zone for the instance
    # already exists.  If it does, check to see whether
    # the reverse lookup zone is associated with the instance's
    # VPC.  If it isn't create the association.  You don't
    # need to do this when you create the reverse lookup
    # zone because the association is done automatically.
    LOGGER.info("reversed_lookup_zone: %s", str(reversed_lookup_zone) + lineno())
    reverse_zone = None
    for record in hosted_zones['HostedZones']:
        LOGGER.debug("record name: %s", str(record['Name']) + lineno())
        if record['Name'] == reversed_lookup_zone:
            reverse_zone = record['Name']
            break
    if reverse_zone:
        LOGGER.debug("Reverse lookup zone found: %s", str(reversed_lookup_zone) + lineno())
        reverse_lookup_zone_id = get_zone_id(route53, reversed_lookup_zone)
        LOGGER.debug("reverse_lookup_zone_id: %s", str(reverse_lookup_zone_id) + lineno())

        reverse_hosted_zone_properties = get_hosted_zone_properties(route53, reverse_lookup_zone_id)
        LOGGER.debug("reverse_hosted_zone_properties:"
                     " %s", str(reverse_hosted_zone_properties) + lineno())

        if vpc_id in map(lambda x: x['VPCId'], reverse_hosted_zone_properties['VPCs']):
            LOGGER.info("Reverse lookup zone %s is associated \
with VPC %s %s", reverse_lookup_zone_id, vpc_id, lineno())
        else:
            LOGGER.info("Associating zone %s with VPC %s", reverse_lookup_zone_id, vpc_id)
            try:
                associate_zone(route53, reverse_lookup_zone_id, region, vpc_id)
            except BaseException as err:
                publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                               str(err) + lineno())
                LOGGER.debug("%s", str(err) + lineno())
    else:
        LOGGER.info("No matching reverse lookup zone, so we will create one %s", lineno())
        # create private hosted zone for reverse lookups
        if state == 'running':
            create_reverse_lookup_zone(route53, instance, reversed_domain_prefix, region)
            reverse_lookup_zone_id = get_zone_id(route53, reversed_lookup_zone)




    # Is there a DHCP option set?
    # Get DHCP option set configuration
    LOGGER.debug("\n#############\nIterate over DHCP option sets %s\n", lineno())

    try:
        LOGGER.debug("trying to get dhcp option set id %s", lineno())
        dhcp_options_id = get_dhcp_option_set_id_for_vpc(compute, vpc_id)
        LOGGER.debug("dhcp_options_id: %s", str(dhcp_options_id) + lineno())
        dhcp_configurations = get_dhcp_configurations(compute, dhcp_options_id)
        LOGGER.debug("dhcp_configurations: %s", str(get_dhcp_configurations) + lineno())

    except BaseException as err:
        LOGGER.info("No DHCP option set assigned to this VPC %s\n", str(err) + lineno())
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(err) + lineno())
        exit()

    # Look to see whether there's a DHCP option set assigned to
    # the VPC.  If there is, use the value of the domain name
    # to create resource records in the appropriate Route 53
    # private hosted zone. This will also check to see whether
    # there's an association between the instance's VPC and
    # the private hosted zone.  If there isn't, it will create it.
    for configuration in dhcp_configurations:

        LOGGER.debug("configuration: %s", str(configuration) + lineno())
        LOGGER.debug("private hosted zones: %s", str(private_hosted_zone_collection) + lineno())

        if configuration in private_hosted_zone_collection:
            private_hosted_zone_name = configuration
            LOGGER.debug("Private zone found %s", str(private_hosted_zone_name) + lineno())

            private_hosted_zone_id = get_zone_id(route53, private_hosted_zone_name)
            LOGGER.debug("Private_hosted_zone_id: %s", str(private_hosted_zone_id) + lineno())
            private_hosted_zone_properties = get_hosted_zone_properties(
                route53,
                private_hosted_zone_id
            )

            LOGGER.debug("private_hosted_zone_properties:"
                         " %s", str(private_hosted_zone_properties) + lineno())

            # create A records and PTR records
            if state == 'running':
                if vpc_id in map(lambda x: x['VPCId'], private_hosted_zone_properties['VPCs']):
                    LOGGER.info("Private hosted zone %s is associated \
with VPC %s %s", private_hosted_zone_id, vpc_id, lineno())
                else:
                    LOGGER.info("Associating zone %s with VPC"
                                " %s %s", private_hosted_zone_id, vpc_id, lineno())
                    try:
                        associate_zone(route53, private_hosted_zone_id, region, vpc_id)
                    except BaseException as err:
                        LOGGER.info("You cannot create an association with a \
                        VPC with an overlapping subdomain. %s\n", str(err))
                        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                                       str(err) + lineno())
                        exit()
                try:
                    LOGGER.info("Creating resource records %s", lineno())
                    create_resource_record(
                            route53,
                            private_hosted_zone_id,
                            private_host_name,
                            private_hosted_zone_name,
                            'A',
                            private_ip
                    )

                    LOGGER.info('Created A record: '+
                                               str(private_host_name) + '.' +
                                               str(private_hosted_zone_name) +' in zone id: ' +
                                               str(private_hosted_zone_id) +
                                               ' for hosted zone ' +
                                               str(private_hosted_zone_name) +
                                               ' with value: ' +
                                               str(private_ip))

                    create_resource_record(
                            route53,
                            reverse_lookup_zone_id,
                            reversed_ip_address,
                            'in-addr.arpa',
                            'PTR',
                            private_host_name
                    )

                    LOGGER.info('Created PTR record in zone id: ' +
                                               str(reverse_lookup_zone_id) + ' for hosted zone ' + str(reversed_lookup_zone) +
                                               ' with value: ' +
                                               str(reversed_ip_address))
                except BaseException as err:
                    publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                                   str(err) + lineno())





        else:
            LOGGER.debug("No matching zone for %s", str(configuration) + lineno())







def get_instances(client, instance_id):
    """
    Get ec2 instance information
    :return:
    """
    try:
        return client.describe_instances(InstanceIds=[instance_id])
    except ClientError as err:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(err) + lineno())


def list_hosted_zones(client):
    """
    Get route53 hosted zones
    :param client:
    :return:
    """
    try:
        return client.list_hosted_zones()
    except ClientError as err:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(err) + lineno())


def list_tables(client):
    """
    List the dynamodb tables
    :param client:
    :return:
    """
    try:
        return client.list_tables()
    except ClientError as err:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(err) + lineno())

def put_item_in_dynamodb_table(client, table, instance_id, instance_attributes):
    """
    Put item in dynamodb table
    :param client:
    :param table:
    :param instance_id:
    :param instance_attributes:
    :return:
    """
    try:
        LOGGER.debug("attributes: %s", str(instance_attributes) + lineno())
        LOGGER.debug("putting attributes: %s", str(instance_attributes) + lineno())

        return client.put_item(
            TableName=str(table),
            Item={
                'InstanceId': {'S': instance_id},
                'InstanceAttributes': {'S': str(instance_attributes)}
            }
        )
    except ClientError as err:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(err) + lineno())


def get_item_from_dynamodb_table(client, table, instance_id):
    """
    Get item from dynamodb table
    :param client:
    :param table:
    :param instance_id:
    :return:
    """
    try:
        # Fetch item from DynamoDB
        item = client.get_item(
            TableName=table,
            Key={
                'InstanceId': {
                    'S': instance_id
                }
            },
            AttributesToGet=[
                'InstanceAttributes'
            ]
        )

        if 'Item' in item:
            LOGGER.debug("returned item:"
                         " %s", str(item['Item']['InstanceAttributes']['S']) + lineno())
            item = item['Item']['InstanceAttributes']['S'].replace("'", '"')
            item = item.replace(" True,", ' "True",')
            item = item.replace(" False,", ' "False",')
            LOGGER.debug("item: %s", str(item) + lineno())
            return json.loads(item)
        return None
    except ClientError as err:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(err))


def get_private_hosted_zone_collection(private_hosted_zones):
    """
    Get private hosted zone collection
    :param private_hosted_zones:
    :return:
    """
    try:
        private_hosted_zone_collection = []

        for item in private_hosted_zones:
            LOGGER.debug("item: %s", str(item) + lineno())
            private_hosted_zone_collection.append(item['Name'])

        return private_hosted_zone_collection
    except:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(sys.exc_info()[0]) + lineno())






def get_private_hosted_zones(hosted_zones):
    """
    Get private hosted zones
    :param hosted_zones:
    :return:
    """
    try:
        private_hosted_zones = []

        for item in hosted_zones['HostedZones']:
            LOGGER.debug("item: %s", str(item) + lineno())

            if item['Config']['PrivateZone']:
                private_hosted_zones.append(item)

        return private_hosted_zones
    except:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(sys.exc_info()[0]) + lineno())


def get_dhcp_option_set_id_for_vpc(client, vpc_id):
    """
    Get the dhcp option set from vpc
    :param client:
    :param vpc_id:
    :return:
    """
    try:
        option_sets = {}

        results = client.describe_vpcs()

        for item in results['Vpcs']:

            if 'DhcpOptionsId' in item:
                option_sets[str(item['VpcId'])] = item['DhcpOptionsId']
            else:
                option_sets[str(item['VpcId'])] = None

        return option_sets[vpc_id]

    except ClientError as err:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(err) + lineno())


def create_dynamodb_table(client, table_name):
    """
    Create dynamodb table
    :param client:
    :param table_name:
    :return:
    """
    try:
        return client.create_table(
            TableName=table_name,
            AttributeDefinitions=[
                {
                    'AttributeName': 'InstanceId',
                    'AttributeType': 'S'
                },
            ],
            KeySchema=[
                {
                    'AttributeName': 'InstanceId',
                    'KeyType': 'HASH'
                },
            ],
            ProvisionedThroughput={
                'ReadCapacityUnits': 4,
                'WriteCapacityUnits': 4
            }
        )
    except ClientError as err:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(err) + lineno())


def get_dynamodb_table(client, table_name):
    """
    Get the dynamodb table
    :param client:
    :param table_name:
    :return:
    """
    try:
        return client.describe_table(
            TableName=table_name
        )
    except ClientError as err:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(sys.exc_info()[0]) + lineno())


def create_table(client, table_name):
    """
    Create dynamodb table
    :param client:
    :param table_name:
    :return:
    """
    try:
        create_dynamodb_table(client, table_name)
        created = -1
        while created < 0:
            table = get_dynamodb_table(client, table_name)

            if table['Table']['TableStatus'] == 'ACTIVE':
                created = 1
            else:
                time.sleep(0/1000)

        return True
    except:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(sys.exc_info()[0]) + lineno())


def change_resource_recordset(client, zone_id, host_name, hosted_zone_name, record_type, value):
    """
    Change resource recordset
    :param client:
    :param zone_id:
    :param host_name:
    :param hosted_zone_name:
    :param value:
    :return:
    """
    try:
        response = client.change_resource_record_sets(
            HostedZoneId=zone_id,
            ChangeBatch={
                "Comment": "Updated by Lambda DDNS",
                "Changes": [
                    {
                        "Action": "UPSERT",
                        "ResourceRecordSet": {
                            "Name": host_name + hosted_zone_name,
                            "Type": record_type,
                            "TTL": 60,
                            "ResourceRecords": [
                                {
                                    "Value": value
                                },
                            ]
                        }
                    },
                ]
            }
        )

        LOGGER.debug("response: %s", str(response) + lineno())
        return response
    except ClientError as err:
        LOGGER.debug("Error creating resource record: %s", str(err) + lineno())
        error_message = str(err)

        if "conflicts with other records" in error_message:
            LOGGER.debug("Can not create dns record because of duplicates: %s", str(err) + lineno())
            return 'Duplicate resource record'
        elif "conflicting RRSet" in error_message:
            LOGGER.debug("Can not create dns record because of duplicates: %s", str(err) + lineno())
            return 'Conflicting resource record'
        else:
            publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" + str(err) + lineno())
            return 'Unexpected error: ' + str(err)


def create_resource_record(client, zone_id, host_name, hosted_zone_name, record_type, value):
    """
    This function creates resource records in the hosted zone passed by the calling function.
    :param client:
    :param zone_id:
    :param host_name:
    :param hosted_zone_name:
    :param record_type:
    :param value:
    :return:
    """
    LOGGER.debug("Creating resource record: zone_id: %s host_name:"
                 " %s hosted_zone_name: %s record_type: %s value: %s %s", zone_id,
                 host_name, hosted_zone_name, record_type, value, lineno())
    try:
        if host_name[-1] != '.':
            host_name = host_name + '.'

        LOGGER.debug(
            "Updating %s in zone %s%s to %s %s", record_type, host_name,
            hosted_zone_name, value, lineno())

        # To prevent rate throttling
        time.sleep(1/1000)

        response = change_resource_recordset(
            client,
            zone_id,
            host_name,
            hosted_zone_name,
            record_type,
            value
        )

        LOGGER.debug("response: %s", str(response) + lineno())
        return response
    except ClientError as err:
        LOGGER.debug("Error creating resource record: %s", str(err) + lineno())
        if 'is not permitted as it conflicts with other records ' \
           'with the same DNS name in zone' in str(err):
            LOGGER.debug("Can not create dns record because "
                         "of duplicates: %s", str(err) + lineno())

        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" + str(err) + lineno())




    except ClientError as err:
        if 'Not Found' in str(err):
            LOGGER.debug("Record not found error: %s", str(err) + lineno())
            return

        if 'InvalidChangeBatch' in str(err) and 'it was not found' in str(err):
            LOGGER.debug("Record not found error: %s", str(err) + lineno())
            return

        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(sys.exc_info()[0]) + lineno())


def get_zone_id(client, zone_name, private_zone=True):
    """
    This function returns the zone id for the zone name that's passed into the function.
    :param client:
    :param zone_name:
    :return:
    """
    try:
        if zone_name[-1] != '.':
            zone_name = zone_name + '.'
        hosted_zones = list_hosted_zones(client)

        LOGGER.debug("zone name: %s", str(zone_name) + lineno())
        LOGGER.debug("hosted_zones: %s", str(hosted_zones) + lineno())
        zones = []
        for record in hosted_zones['HostedZones']:
            LOGGER.debug("record: %s", str(record) + lineno())
            if record['Config']['PrivateZone'] == private_zone:
                if record['Name'] == zone_name:
                    zones.append(record)
        LOGGER.debug("zones: %s", str(zones) + lineno())

        try:
            zone_id_long = zones[0]['Id']
            LOGGER.debug("zone id: %s", str(zone_id_long) + lineno())
            zone_id = str.split(str(zone_id_long), '/')[2]
            return zone_id
        except:
            return None
    except:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(sys.exc_info()[0]) + lineno())





def get_dhcp_configurations(client, dhcp_options_id):
    """
    This function returns the names of the zones/domains that are in the option set.
    :param client:
    :param dhcp_options_id:
    :return:
    """
    try:
        zone_names = []

        response = client.describe_dhcp_options(
            DhcpOptionsIds=[
                str(dhcp_options_id)
            ]
        )
        LOGGER.debug("response: %s", str(response) + lineno())
        dhcp_configurations = response['DhcpOptions'][0]['DhcpConfigurations']
        LOGGER.debug("dhcp_configurations: %s", str(dhcp_configurations) + lineno())
        for configuration in dhcp_configurations:
            for item in configuration['Values']:
                LOGGER.debug("item: %s", str(item) + lineno())
                zone_names.append(str(item['Value']) + '.')
        LOGGER.debug("zone name: %s", str(zone_names) + lineno())
        return zone_names
    except:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(sys.exc_info()[0]) + lineno())


def reverse_list(ip_list):
    """
    Reverses the order of the instance's IP address and
    helps construct the reverse lookup zone name.
    :param list:
    :return:
    """
    try:
        if (re.search(r"\d{1,3}.\d{1,3}.\d{1,3}.\d{1,3}", ip_list)) or \
                (re.search(r"\d{1,3}.\d{1,3}.\d{1,3}\.", ip_list)) or \
                (re.search(r"\d{1,3}.\d{1,3}\.", ip_list)) or \
                (re.search(r"\d{1,3}\.", ip_list)):
            my_temp_list = str.split(str(ip_list), '.')
            LOGGER.debug("temp list: %s", str(my_temp_list) + lineno())
            my_list = []
            for item in my_temp_list:
                LOGGER.debug("item: %s", str(item) + lineno())
                if len(item) > 0:
                    my_list.append(int(item))

            LOGGER.debug("list1: %s", str(my_list) + lineno())
            LOGGER.debug("type: %s", str(type(my_list)) + lineno())

            my_list.reverse()
            reversed_list = ''
            for item in my_list:
                reversed_list = reversed_list + str(item) + '.'
            LOGGER.debug("returning: %s", str(reversed_list) + lineno())
            return reversed_list

        LOGGER.info('Not a valid ip')
        exit()
    except:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(sys.exc_info()[0]) + lineno())


def get_reversed_domain_prefix(subnet_mask, private_ip):
    """
    Uses the mask to get the zone prefix for the reverse lookup zone
    :param subnet_mask:
    :param private_ip:
    :return:
    """
    try:
        LOGGER.debug("### Subnet mask: %s", str(subnet_mask) + lineno())
        LOGGER.debug("### Private ip: %s", str(private_ip) + lineno())

        third_octet = re.search(r"\d{1,3}.\d{1,3}.\d{1,3}.", private_ip)
        return third_octet.group(0)
    except:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(sys.exc_info()[0]) + lineno())


def create_reverse_lookup_zone(client, instance, reversed_domain_prefix, region):
    """
    Creates the reverse lookup zone.
    :param client:
    :param instance:
    :param reversed_domain_prefix:
    :param region:
    :return:
    """
    try:
        LOGGER.debug('Creating reverse lookup zone %s in.addr.arpa.'
                     ' %s', str(reversed_domain_prefix), lineno())

        if reversed_domain_prefix[-1] == ".":
            reversed_domain_prefix = reversed_domain_prefix[:-1]

        return client.create_hosted_zone(
            Name=reversed_domain_prefix + '.in-addr.arpa.',
            VPC={
                'VPCRegion': region,
                'VPCId': instance['Reservations'][0]['Instances'][0]['VpcId']
            },
            CallerReference=str(uuid.uuid1()),
            HostedZoneConfig={
                'Comment': 'Updated by Lambda DDNS'
            }
        )
    except:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(sys.exc_info()[0]) + lineno())


def json_serial(obj):
    """
    JSON serializer for objects not serializable by default json code
    :param obj:
    :return:
    """
    try:
        if isinstance(obj, datetime.datetime):
            serial = obj.isoformat()
            return serial
    except:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(sys.exc_info()[0]) + lineno())


def remove_empty_from_dict(dictionary):
    """
    Removes empty keys from dictionary
    :param d:
    :return:
    """

    try:
        if isinstance(dictionary, dict):
            return dict((k, remove_empty_from_dict(v)) for k, v in dictionary.items() \
                        if v and remove_empty_from_dict(v))
        if isinstance(dictionary, list):
            return [remove_empty_from_dict(v) for v in dictionary
                    if v and remove_empty_from_dict(v)]

        return dictionary
    except:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(sys.exc_info()[0]) + lineno())


def associate_zone(client, hosted_zone_id, region, vpc_id):
    """
    Associates private hosted zone with VPC
    :param client:
    :param hosted_zone_id:
    :param region:
    :param vpc_id:
    :return:
    """
    try:
        return client.associate_vpc_with_hosted_zone(
            HostedZoneId=hosted_zone_id,
            VPC={
                'VPCRegion': region,
                'VPCId': vpc_id
            },
            Comment='Updated by Lambda DDNS'
        )
    except:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(sys.exc_info()[0]) + lineno())


def is_dns_hostnames_enabled(client, vpc_id):
    """
    Whether dns hostnames is enabled
    :param client:
    :param vpc_id:
    :return:
    """
    try:
        response = client.describe_vpc_attribute(
            Attribute='enableDnsHostnames',
            VpcId=vpc_id
        )

        LOGGER.debug("%s", str(response) + lineno())
        return response['EnableDnsHostnames']['Value']
    except:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(sys.exc_info()[0]) + lineno())


def is_dns_support_enabled(client, vpc_id):
    """
    Whether dns support is enabled
    :param client:
    :param vpc_id:
    :return:
    """
    try:
        response = client.describe_vpc_attribute(
            Attribute='enableDnsSupport',
            VpcId=vpc_id
        )

        LOGGER.debug('response2: %s', str(response) + lineno())
        return response['EnableDnsSupport']['Value']
    except:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(sys.exc_info()[0]) + lineno())


def get_hosted_zone_properties(client, zone_id):
    """
    Get hosted zone properties
    :param client:
    :param zone_id:
    :return:
    """
    try:
        LOGGER.debug('getting hosted zone properties: zone_id: %s', str(zone_id) + lineno())
        hosted_zone_properties = client.get_hosted_zone(Id=zone_id)
        LOGGER.debug('hosted_zone_properties: %s', str(hosted_zone_properties) + lineno())
        if 'ResponseMetadata' in hosted_zone_properties:
            hosted_zone_properties.pop('ResponseMetadata')
        return hosted_zone_properties
    except:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(sys.exc_info()[0]) + lineno())


def get_subnet_cidr_block(client, subnet_id):
    """
    Get subnect cidr block
    :param client:
    :param subnet_id:
    :return:
    """
    try:
        response = client.describe_subnets(
            SubnetIds=[
                subnet_id
            ]
        )
        return response['Subnets'][0]['CidrBlock']
    except:
        publish_to_sns(SNS_CLIENT, ACCOUNT, REGION, "Unexpected error:" +
                       str(sys.exc_info()[0]) + lineno() + lineno())


def publish_to_sns(client, account, region, message):
    """
    Publish a simple message to the specified SNS topic
    :param client:
    :param account:
    :param region:
    :param message:
    :return:
    """
    LOGGER.debug("SNS message: %s ", str(message) + lineno())
    try:
        client.publish(
            TopicArn='arn:aws:sns:' + str(region) + ':' + str(account) + ':DDNSAlerts',
            Message=str(message)
        )
    except ClientError as err:
        LOGGER.debug("Unexpected error: %s", str(err) + lineno())
