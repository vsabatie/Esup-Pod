import os
from django.conf import settings
from pod.main.tasks import task_start_bbb_encode
from pod.bbb.models import Meeting

import subprocess
import time

import threading
import logging

# Use of BigBlueButton
USE_BBB = getattr(settings, "USE_BBB", False)
# Directory of bbb-recorder plugin
DEFAULT_BBB_PLUGIN = getattr(
    settings, "DEFAULT_BBB_PLUGIN", "/data/bbb-recorder/"
)
# Directory that will contain the video files generated by bbb-recorder
DEFAULT_BBB_PATH = getattr(
    settings, "DEFAULT_BBB_PATH", "/data/bbb-recorder/media/"
)
# The last caracter of DEFAULT_BBB_PATH must be an OS separator
if not DEFAULT_BBB_PATH.endswith(os.path.sep):
    DEFAULT_BBB_PATH += os.path.sep

# BigBlueButton or Scalelite server URL, where BBB Web presentation and API are
BBB_SERVER_URL = getattr(
    settings, "BBB_SERVER_URL", "https://bbb.univ-test.fr/"
)
# The last caracter of BBB_SERVER_URL must be /
if not BBB_SERVER_URL.endswith("/"):
    BBB_SERVER_URL += "/"

# Debug mode
DEBUG = getattr(settings, "DEBUG", False)
# Use of Celery to encode
CELERY_TO_ENCODE = getattr(settings, "CELERY_TO_ENCODE", False)

log = logging.getLogger(__name__)


def start_bbb_encode(id):
    if CELERY_TO_ENCODE:
        task_start_bbb_encode.delay(id)
    else:
        log.info("START BBB ENCODE MEETING %s" % id)
        t = threading.Thread(target=bbb_encode_meeting, args=[id])
        t.setDaemon(True)
        t.start()


def bbb_encode_meeting(id):
    msg = ""

    # Get the meeting
    meeting_to_encode = Meeting.objects.get(id=id)

    # Update this meeting and put
    # encoding_step to 2 (Encoding in progress)
    meeting_to_encode.encoding_step = 2
    meeting_to_encode.save()

    # Encode in webm (not mp4, less data in log)
    command = ""
    # Put on the bbb-recorder plugin directory
    command += "cd %s; " % (DEFAULT_BBB_PLUGIN)
    # The command looks like :
    # node export.js https://bbb.univ.fr/playback/presentation/2.0/
    # playback.html?meetingId=INTERNAL_MEETING_ID
    # > /data/www/USERPOD/bbb-recorder/logs/INTERNAL_MEETING_ID.log
    # 2>&1 < /dev/null
    command += "node export.js " + str(meeting_to_encode.recording_url)
    command += " > " + DEFAULT_BBB_PATH + "logs/"
    command += str(meeting_to_encode.internal_meeting_id)
    command += ".log 2>&1 < /dev/null"

    # if you want to reuse the command : print(command)
    msg = "\nBBBEncodeCommand :\n%s" % command
    msg += "\n- Encoding : %s" % time.ctime()
    # Execute the process
    subprocess.run(
        command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    msg += "\n- meeting_to_encode : %s" % meeting_to_encode
    msg += "\n- End Encoding : %s" % time.ctime()

    # Update this meeting and put
    # encoding_step to 3 (Encoded)
    meeting_to_encode.encoding_step = 3
    meeting_to_encode.save()
