This project will provide the user with a web based UI that will allow them to upload a file into an S3 bucket called "upload", a back end lambda function "filescan" will then randomly move the file to either of the S3 buckets "clean" or "quarantine". The webpage will display all clean and quarantine files for the user to view.

The project will use the AWS Emulator on my local Docker, terraform will be used to set up all AWS resources. The lambda fuctions will be written in Java and use micronaut.

The project structure will be broken down into terraform, lamdba and web modules. The lamdba module will have submodules for each function. This will be a maven project.

When the user attempts to upload a document, the file gateway lamdba will provide a pre-signed URL to allow the document to be uploaded to the upload bucket. 

An sqs queue will alert the filescan lambda that a document has been uploaded to the upload bucket and is ready for processing. The function will simulate a virus scan by randomly marking the document as clean or quarantine and move the document to the required s3 bucket. The document filename and where it has been assigned will be recorded in DynamoDB table

The UI will be updated to inform the user if their document is clean or quarantined, and provide a link to that document in it's S3 bucket