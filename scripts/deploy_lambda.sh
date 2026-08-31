#!/bin/bash
# Deploy AccessFlow Lambda infrastructure
# Run from project root: ./scripts/deploy_lambda.sh

set -e

REGION="${AWS_REGION:-us-west-2}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
FUNCTION_NAME="accessflow-poller"
ROLE_NAME="accessflow-poller-role"
FINGERPRINT_TABLE="accessflow-fingerprints"
BUDGET_TABLE="accessflow-budget"
BUCKET_NAME="accessflow-agenda-cache-${ACCOUNT_ID}"
QUEUE_NAME="accessflow-overflow"

echo "Deploying AccessFlow Lambda infrastructure..."
echo "Region: ${REGION}"
echo "Account: ${ACCOUNT_ID}"

# 1. Create DynamoDB tables
echo ""
echo "Creating DynamoDB tables..."

aws dynamodb create-table \
    --table-name "$FINGERPRINT_TABLE" \
    --attribute-definitions AttributeName=meeting_key,AttributeType=S \
    --key-schema AttributeName=meeting_key,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "$REGION" 2>/dev/null || echo "Table $FINGERPRINT_TABLE already exists"

aws dynamodb create-table \
    --table-name "$BUDGET_TABLE" \
    --attribute-definitions AttributeName=date,AttributeType=S \
    --key-schema AttributeName=date,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "$REGION" 2>/dev/null || echo "Table $BUDGET_TABLE already exists"

# 2. Create S3 bucket
echo ""
echo "Creating S3 bucket..."
aws s3 mb "s3://${BUCKET_NAME}" --region "$REGION" 2>/dev/null || echo "Bucket $BUCKET_NAME already exists"

# 3. Create SQS queue
echo ""
echo "Creating SQS queue..."
QUEUE_URL=$(aws sqs create-queue \
    --queue-name "$QUEUE_NAME" \
    --region "$REGION" \
    --query 'QueueUrl' --output text 2>/dev/null || \
    aws sqs get-queue-url --queue-name "$QUEUE_NAME" --region "$REGION" --query 'QueueUrl' --output text)
echo "Queue URL: $QUEUE_URL"

# 4. Create IAM role
echo ""
echo "Creating IAM role..."

TRUST_POLICY='{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Service": "lambda.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }
  ]
}'

aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document "$TRUST_POLICY" 2>/dev/null || echo "Role $ROLE_NAME already exists"

# Attach policies
aws iam attach-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole" 2>/dev/null || true

# Create inline policy for DynamoDB, S3, SQS, Bedrock
POLICY='{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["dynamodb:*"],
      "Resource": [
        "arn:aws:dynamodb:'$REGION':'$ACCOUNT_ID':table/'$FINGERPRINT_TABLE'",
        "arn:aws:dynamodb:'$REGION':'$ACCOUNT_ID':table/'$BUDGET_TABLE'"
      ]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:*"],
      "Resource": [
        "arn:aws:s3:::'$BUCKET_NAME'",
        "arn:aws:s3:::'$BUCKET_NAME'/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": ["sqs:SendMessage"],
      "Resource": "arn:aws:sqs:'$REGION':'$ACCOUNT_ID':'$QUEUE_NAME'"
    },
    {
      "Effect": "Allow",
      "Action": ["bedrock:InvokeAgent"],
      "Resource": "*"
    }
  ]
}'

aws iam put-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name "accessflow-policy" \
    --policy-document "$POLICY"

# Wait for role to propagate
echo "Waiting for IAM role to propagate..."
sleep 10

# 5. Package Lambda
echo ""
echo "Packaging Lambda function..."
cd "$(dirname "$0")/.."
rm -rf .lambda-package
mkdir -p .lambda-package

# Copy backend code
cp -r backend .lambda-package/

# Install dependencies
pip3 install -t .lambda-package/ httpx pypdf boto3 strands-agents pydantic --quiet

# Create zip
cd .lambda-package
zip -r ../lambda-package.zip . -x "*.pyc" -x "__pycache__/*" -x "*.egg-info/*" --quiet
cd ..

# 6. Create/Update Lambda function
echo ""
echo "Creating Lambda function..."
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

aws lambda create-function \
    --function-name "$FUNCTION_NAME" \
    --runtime python3.12 \
    --role "$ROLE_ARN" \
    --handler "backend.poller.poller_handler.handler" \
    --zip-file fileb://lambda-package.zip \
    --timeout 60 \
    --memory-size 512 \
    --environment "Variables={FINGERPRINT_TABLE=$FINGERPRINT_TABLE,BUDGET_TABLE=$BUDGET_TABLE,AGENDA_CACHE_BUCKET=$BUCKET_NAME,OVERFLOW_QUEUE_URL=$QUEUE_URL,BUDGET_STORAGE=dynamodb,AGENDA_CACHE_ENABLED=true,MODEL_PROVIDER=bedrock,DAILY_USD_CAP=1.50}" \
    --region "$REGION" 2>/dev/null || \
    aws lambda update-function-code \
        --function-name "$FUNCTION_NAME" \
        --zip-file fileb://lambda-package.zip \
        --region "$REGION"

# Update environment if function existed
aws lambda update-function-configuration \
    --function-name "$FUNCTION_NAME" \
    --environment "Variables={FINGERPRINT_TABLE=$FINGERPRINT_TABLE,BUDGET_TABLE=$BUDGET_TABLE,AGENDA_CACHE_BUCKET=$BUCKET_NAME,OVERFLOW_QUEUE_URL=$QUEUE_URL,BUDGET_STORAGE=dynamodb,AGENDA_CACHE_ENABLED=true,MODEL_PROVIDER=bedrock,DAILY_USD_CAP=1.50}" \
    --region "$REGION" 2>/dev/null || true

# 7. Create EventBridge rule
echo ""
echo "Creating EventBridge rule..."
RULE_NAME="accessflow-poller-schedule"

aws events put-rule \
    --name "$RULE_NAME" \
    --schedule-expression "rate(15 minutes)" \
    --state ENABLED \
    --description "Trigger AccessFlow poller every 15 minutes" \
    --region "$REGION"

# Add Lambda as target
LAMBDA_ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${FUNCTION_NAME}"

aws events put-targets \
    --rule "$RULE_NAME" \
    --targets "Id=1,Arn=$LAMBDA_ARN" \
    --region "$REGION"

# Add permission for EventBridge to invoke Lambda
aws lambda add-permission \
    --function-name "$FUNCTION_NAME" \
    --statement-id "eventbridge-invoke" \
    --action "lambda:InvokeFunction" \
    --principal "events.amazonaws.com" \
    --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/${RULE_NAME}" \
    --region "$REGION" 2>/dev/null || true

# 8. Verify no VPC
echo ""
echo "Verifying Lambda configuration (no VPC)..."
aws lambda get-function-configuration \
    --function-name "$FUNCTION_NAME" \
    --region "$REGION" \
    --query '{FunctionName:FunctionName,Runtime:Runtime,MemorySize:MemorySize,Timeout:Timeout,VpcConfig:VpcConfig}' \
    --output json

# Cleanup
rm -rf .lambda-package lambda-package.zip

echo ""
echo "Deployment complete!"
echo ""
echo "To check function config:"
echo "  aws lambda get-function-configuration --function-name $FUNCTION_NAME --region $REGION"
echo ""
echo "To test manually:"
echo "  aws lambda invoke --function-name $FUNCTION_NAME --region $REGION /tmp/out.json && cat /tmp/out.json"
