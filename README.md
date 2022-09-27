# Building a Dynamic DNS for Route 53 using CloudWatch Events and Lambda

Credits to   Jeremy Cowan, Efrain Fuentes and Bryan Liston from AWS

https://aws.amazon.com/blogs/compute/building-a-dynamic-dns-for-route-53-using-cloudwatch-events-and-lambda/

https://github.com/aws-samples/aws-lambda-ddns-function

## In my company we needed a dynamic way to add A records to a Route private hosted zone, based on the value of the Tag Name, I have modified the original code to this purpose. 

- This script will perform the following functions.

  If the instance is running in a VPC with a custom dhcp option set with a dns domain name that matches a privated hosted zone created in route53,
     
      1. Creates an 'A' record using the Tag "Name" value is created to the secondary IP,
         if no Tag "Name" then it uses private_DNS_name,
         if no secondary IP then it uses the primary IP
      2. If the PTR Zone doesnt exits, it creates the PTR zone, associates it with the VPC and creates A 'PTR" record to the DNS name,

- Creates a dynamodb table with all not empty properties of the instance excluding metadata

## DDNS/Lambda example

Make sure that you have the latest version of the AWS CLI installed locally.   For more information, see [Getting Set Up with the AWS Command Line Interface](http://docs.aws.amazon.com/cli/latest/userguide/cli-chap-getting-set-up.html).

For this example, create a new VPC configured with a private and public subnet, using [Scenario 2: VPC with Public and Private Subnets (NAT)](http://docs.aws.amazon.com/AmazonVPC/latest/UserGuide/VPC_Scenario2.html) from the Amazon VPC User Guide.  Ensure that the VPC has the **DNS resolution** and **DNS hostnames** options set to **yes**.

After the VPC is created, you can proceed to the next steps.

##### Step 1 – Create an IAM role for the Lambda function

In this step, you will use the AWS Command Line Interface (AWS CLI) to create the Identity and Access Management (IAM) role that the Lambda function assumes when the function is invoked.  You also need to create an IAM policy with the required permissions and then attach this policy to the role.

1) Download the **ddns-policy.json** and **ddns-trust.json** files from the [AWS Labs GitHub repo](https://github.com/awslabs/aws-lambda-ddns-function).

_ddns-policy.json_

The policy includes **ec2:Describe permission**, required for the function to obtain the EC2 instance’s attributes, including the private IP address, public IP address, and DNS hostname.   The policy also includes DynamoDB and Route 53 full access which the function uses to create the DynamoDB table and update the Route 53 DNS records.  The policy also allows the function to create log groups and log events.
```JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "ec2:Describe*",
    "Resource": "*"
  }, {
    "Effect": "Allow",
    "Action": [
      "dynamodb:*"
    ],
    "Resource": "*"
  }, {
    "Effect": "Allow",
    "Action": [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ],
    "Resource": "*"
  }, {
    "Effect": "Allow",
    "Action": [
      "route53:*"
    ],
    "Resource": [
      "*"
    ]
  },
  {
    "Effect": "Allow",
    "Action": [
        "SNS:Publish"
    ],
    "Resource": [
        { "Fn::Join": ["", [ "arn:aws:sns:",{"Ref":"AWS::Region"}, ":",{"Ref":"AWS::AccountId"},":DDNSAlerts"]]}
    ]
  }]
}
```
_ddns-trust.json_

The **ddns-trust.json** file contains the trust policy that grants the Lambda service permission to assume the role.
```JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "",
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```
2) Create the policy using the policy document in the **ddns-pol.json** file.  You need to replace **\<LOCAL PATH\>** with your local path to the **ddns-pol.json** file.   The output of the **aws iam create-policy** command includes the Amazon Resource Locator (ARN).  Save the ARN since you will need it for future steps.
```
aws iam create-policy --policy-name ddns-lambda-policy --policy-document file://<LOCAL PATH>/ddns-pol.json
```
3) Create the **ddns-lambda-role** IAM role using the trust policy in the **ddns-trust.json** file.  You need to replace **\<LOCAL PATH\>** with your local path to the **ddns-trust.json** file.  The output of the **aws iam create-role** command includes the ARN associated with the role that you created.  Save this ARN since you will need it when you create the Lambda function in the next section.
```
aws iam create-role --role-name ddns-lambda-role --assume-role-policy-document file://<LOCAL PATH>/ddns-trust.json
```
4) Attach the policy to the role.  Use the ARN returned in step 2 for the **--policy-arn** input parameter.
```
aws iam attach-role-policy --role-name ddns-lambda-role --policy-arn <enter-your-policy-arn-here>
```
##### Step 2 – Create the Lambda function

The Lambda function uses modules included in the Python 3.8 Standard Library and the AWS SDK for Python module (boto3), which is preinstalled as part of the Lambda service.  As such, you do not need to create a deployment package for this function.

The code performs the following:

-	Checks to see whether the “DDNS” table exists in DynamoDB and creates the table if it does not. This table is used to keep a record of instances that have been created along with their attributes. It’s necessary to persist the instance attributes in a table because once an EC2 instance is terminated, its attributes are no longer available to be queried via the EC2 API. Instead, they must be fetched from the table.

-	Queries the event data to determine the instance's state. If the state is “running”, the function queries the EC2 API for the data it will need to update DNS. 

-	Verifies that “DNS resolution” and “DNS hostnames” are enabled for the VPC, as these are required in order to use Route 53 for private name resolution.  The function then checks whether a reverse lookup zone for the instance already exists.  If it does, it checks to see whether the reverse lookup zone is associated with the instance's VPC.  If it isn't, it creates the association.  This association is necessary in order for the VPC to use Route 53 zone for private name resolution.

-	Verifies whether there's a DHCP option set assigned to the VPC.  If there is, it uses the value of the domain name to create resource records in the appropriate Route 53 private hosted zone.  The function also checks to see whether there's an association between the instance's VPC and the private hosted zone.  If there isn't, it creates it.


Use the AWS CLI to create the Lambda function:

1) Download the **union.py** file from this [repo](https://github.com/caedh121/dyndns).

2) Create a ZIP archive **union.zip** for **union.py**

```
zip union.zip union.py
```

3) Execute the following command to create the function.  Note that you will need to update the command to use the ARN of the role that you created earlier, as well as the local path to the union.zip file containing the Python code for the Lambda function.
```
aws lambda create-function --function-name ddns_lambda --runtime python3.8 --role <enter-your-role-arn-here> --handler union.lambda_handler --timeout 90 --zip-file fileb://<LOCAL PATH>/union.zip
```
4) The output of the command returns the ARN of the newly-created function.  Save this ARN, since you will need it in the next section.

##### Step 3 – Create the CloudWatch Events Rule

In this step, you create the CloudWatch Events rule that triggers the Lambda function whenever CloudWatch detects a change to the state of an EC2 instance.  You configure the rule to fire when any EC2 instance state changes to “running”, “shutting down”, or “stopped”.  Use the **aws events put-rule** command to create the rule and set the Lambda function as the execution target:
```
aws events put-rule --event-pattern "{\"source\":[\"aws.ec2\"],\"detail-type\":[\"EC2 Instance State-change Notification\"],\"detail\":{\"state\":[\"running\",\"shutting-down\",\"stopped\"]}}" --state ENABLED --name ec2_lambda_ddns_rule
```
The output of the command returns the ARN to the newly created CloudWatch Events rule, named **ec2\_lambda\_ddns\_rule**. Save the ARN, as you will need it to associate the rule with the Lambda function and to set the appropriate Lambda permissions.

Next, set the target of the rule to the Lambda function.  Note that the **--targets** input parameter requires that you include a unique identifier for the **Id** target.  You also need to update the command to use the ARN of the Lambda function that you created previously.
```
aws events put-targets --rule ec2_lambda_ddns_rule --targets Id=id123456789012,Arn=<enter-your-lambda-function-arn-here>
```
Next, you add the permissions required for the CloudWatch Events rule to execute the Lambda function.   Note that you need to provide a unique value for the **--statement-id** input parameter.  You also need to provide the ARN of the CloudWatch Events rule you created earlier.
```
aws lambda add-permission --function-name ddns_lambda --statement-id 45 --action lambda:InvokeFunction --principal events.amazonaws.com --source-arn <enter-your-cloudwatch-events-rule-arn-here>
```
##### Step 4 – Create the private hosted zone in Route 53

To create the private hosted zone in Route 53, follow the steps outlined in [Creating a Private Hosted Zone](http://docs.aws.amazon.com/Route53/latest/DeveloperGuide/hosted-zone-private-creating.html).

##### Step 5 – Create a DHCP options set and associate it with the VPC

In this step, you create a new DHCP options set, and set the domain to be that of your private hosted zone.

1) Follow the steps outlined in [Creating a DHCP Options Set](http://docs.aws.amazon.com/AmazonVPC/latest/UserGuide/VPC_DHCP_Options.html#CreatingaDHCPOptionSet) to create a new set of DHCP options.

2) In the **Create DHCP options set** dialog box, give the new options set a name, set **Domain name** to the name of the private hosted zone that you created in Route 53, and set **Domain name servers** to “AmazonProvidedDNS”.  Choose **Yes, Create**.

![DHCP Option Set](https://github.com/awslabs/aws-lambda-ddns-function/blob/master/images/step-5-4.png)

3) Next, follow the steps outlined in Changing the Set of DHCP Options a VPC Uses to update the VPC to use the newly-created DHCP options set.

##### Step 6 – Launching the EC2 instance and validating results

In this step, you launch an EC2 instance and verify that the function executed successfully.

The Lambda function looks for the Name tag associated with the EC2 instance. If the tag diesn't exist then it uses the private dns name instead.

Because you updated the DHCP options set in this example, the Lambda function uses the specified zone when it creates the Route 53 DNS resource records.

In this example, you launch an EC2 instance into the private subnet of the VPC.  Because you updated the domain value of the DHCP options set to be that of the private hosted zone, the Lambda function creates the DNS resource records in the Route 53 zone file.

_Launching the EC2 instance_

1) Follow the steps to launch an EC2 instance outlined in [Launching an Instance](http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/launching-instance.html).

2) In **Step 3: Configure Instance Details**, for **Network**, select the VPC.  For **Subnet**, select the private subnet.  Choose **Review and Launch**.

Choose **Edit tags** in the **Step 7: Review Instance Launch**.

Enter the key and value for **Step 5: Tag Instance** then choose **Review and Launch**.

![Tag Instance](https://github.com/awslabs/aws-lambda-ddns-function/blob/master/images/step-6-6optional2_1.png)

4) Complete the launch of the instance and wait until the instance state changes to “running”.  Then, continue to the next step.

_Validating results_

In this step, you verify that your Lambda function successfully updated the Rout 53 resource records.

1) Log in to the [Route 53 console](https://console.aws.amazon.com/route53/).

2) In the left navigation pane, choose **Hosted Zones** to view the list of private and public zones currently configured in Route 53.

3) Select the hosted zone that you created in step 4, to view the zone file.

![Hosted Zone](https://github.com/awslabs/aws-lambda-ddns-function/blob/master/images/step-7-4.png)

4) Verify that the resource records were created.

![Resource Records](https://github.com/awslabs/aws-lambda-ddns-function/blob/master/images/step-7-5-1.png)

5) Now that you’ve verified that the Lambda function successfully updated the Route 53 resource records in the zone file, stop the EC2 instance, the records are not deleted.

6) Log in to the [EC2 console](https://console.aws.amazon.com/ec2/).

7) Choose **Instances** in the left navigation pane.

![Instances](https://github.com/awslabs/aws-lambda-ddns-function/blob/master/images/results_6.png)

8) Select the EC2 instance you launched earlier and choose **Stop**.

![Stop Instance](https://github.com/awslabs/aws-lambda-ddns-function/blob/master/images/results_7.png)

9) Follow Steps 1 – 3 to view the DNS resource records in the Route 53 zone.

10) Verify that the records have not been removed from the zone file by the Lambda function.

## Python3 Lambda

The python3 version of the lambda introduces some new features:

* Check the original repo for the SNS setup template  and tox test code.
https://github.com/aws-samples/aws-lambda-ddns-function/blob/master/ddns.template

Thanks again to the authors.
