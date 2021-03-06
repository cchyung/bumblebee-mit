import io
import os

from pydub import AudioSegment
from pydub.utils import mediainfo
from google.cloud import speech
from google.cloud.speech import enums
from google.cloud.speech import types

from google.cloud import storage

from api import db_util

import ffmpy

GCS_BUCKET = "bumblebee-audiofiles"
GCS_RAW_PATH = "raw"
GCS_SNIPPET_PATH = "snippets"
LOCAL_RAW_PATH = GCS_RAW_PATH
LOCAL_SNIPPET_PATH = GCS_SNIPPET_PATH

def speed_change(sound, speed=1.0):
    # Manually override the frame_rate. This tells the computer how many
    # samples to play per second
    sound_with_altered_frame_rate = sound._spawn(sound.raw_data, overrides={
         "frame_rate": int(sound.frame_rate * speed)
      })
     # convert the sound with altered frame rate to a standard frame rate
     # so that regular playback programs will work right. They often only
     # know how to play audio at standard frame rate (like 44.1k)
    return sound_with_altered_frame_rate.set_frame_rate(sound.frame_rate)

def upload_blob(bucket_name, source_file_name, destination_blob_name):
    """Uploads a file to the bucket."""
    storage_client = storage.Client()
    bucket = storage_client.get_bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)

    blob.upload_from_filename(source_file_name)

    print('File {} uploaded to {}.'.format(
        source_file_name,
        destination_blob_name))

def download_blob(bucket_name, source_blob_name, destination_file_name):
    """Downloads a blob from the bucket."""
    storage_client = storage.Client()
    bucket = storage_client.get_bucket(bucket_name)
    blob = bucket.blob(source_blob_name)

    blob.download_to_filename(destination_file_name)

    print('Blob {} downloaded to {}.'.format(
        source_blob_name,
        destination_file_name))

def convert_to_wav(file_name):
    wav_name = file_name.replace(".mp3", ".wav")
    sound = AudioSegment.from_mp3(file_name).set_channels(1)
    # sound = speed_change(sound, 0.7)
    sound.export(wav_name, format="wav")
    upload_blob(GCS_BUCKET, wav_name, wav_name)
    return wav_name


def get_word_infos(file_name):
    gcs_uri = 'gs://%s/%s' % (GCS_BUCKET, file_name)

    speech_client = speech.SpeechClient()

    audio = types.RecognitionAudio(uri=gcs_uri)
    config = types.RecognitionConfig(
        language_code='en-US',
        enable_word_time_offsets=True,
        model="video"
    )

    operation = speech_client.long_running_recognize(config, audio)

    print('Waiting for operation to complete...')
    response = operation.result(timeout=900)

    word_infos = []
    for result in response.results:
        for alternative in result.alternatives:
            for word_info in alternative.words:
                word_infos.append({
                    "word": word_info.word,
                    "start": word_info.start_time.seconds + word_info.start_time.nanos / 1000000000.0,
                    "end": word_info.end_time.seconds + word_info.end_time.nanos / 1000000000.0 + 0.2
                })
    return word_infos

def generate_snippet(file_name, word_info):
    source = AudioSegment.from_wav(file_name)
    snippet = source[word_info["start"]*1000.0:word_info["end"]*1000.0]
    # snippet = speed_change(snippet, (1/0.7))
    snippet_file_name = "%s/%s_%s_%s_%s" % (GCS_SNIPPET_PATH ,word_info["word"], str(word_info["start"]), str(word_info["end"]), file_name.split("/")[1])
    snippet.export(snippet_file_name, format="wav")
    return snippet_file_name


 # '''
 # 1. ) Convert an audio file to single channel wav
 # 2. ) Submit each file to Google Speech API
 # 3. ) Use start and end values to trim audio files into one word snippets
 # 4. ) Upload snippets to GCS
 # 5. ) Call Drew's function to add snippet info to DB
 # '''
def process_file(file_name):
    file_name = convert_to_wav(file_name)
    word_infos = get_word_infos(file_name)

    raw_url = "gs://%s/%s" % (GCS_BUCKET, file_name)

    formatted_file_name = file_name.replace(".mp3", "")
    db_util.create_audio(formatted_file_name, raw_url, "")

    for word_info in word_infos:
        snippet_file_name = generate_snippet(file_name, word_info)
        upload_blob(GCS_BUCKET, snippet_file_name, snippet_file_name)

        snippet_url = "gs://%s/%s" % (GCS_BUCKET, snippet_file_name)

        db_util.create_snippet(word_info["word"], raw_url, snippet_url, int(word_info["start"]), int(word_info["end"]))

def start():
    if not os.path.exists(LOCAL_RAW_PATH):
        os.makedirs(LOCAL_RAW_PATH)

    if not os.path.exists(LOCAL_SNIPPET_PATH):
        os.makedirs(LOCAL_SNIPPET_PATH)

    client = storage.Client()
    bucket=client.get_bucket(GCS_BUCKET)
    blobs=list(bucket.list_blobs(prefix=GCS_RAW_PATH))
    for blob in blobs:
        if(not blob.name.endswith("/")):
            blob.download_to_filename(blob.name)
            process_file(blob.name)
