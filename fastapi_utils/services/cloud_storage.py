from google.cloud import storage


class Uploader:
    def __init__(self):
        self.client = storage.Client()

    def upload(self, bucket_name: str, destination_filename: str, source_filename: str) -> None:
        bucket = self.client.bucket(bucket_name)
        blob = bucket.blob(destination_filename)
        blob.upload_from_filename(source_filename)
