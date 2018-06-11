from django.conf import settings
from django.core.mail import send_mail
from django.core.mail import mail_admins
from django.core.mail import mail_managers
from django.utils.translation import ugettext_lazy as _
from django.core.files.images import ImageFile
from django.core.files import File
from django.apps import apps


from pod.video.models import VideoRendition
from pod.video.models import EncodingVideo
from pod.video.models import EncodingAudio
from pod.video.models import EncodingLog
from pod.video.models import PlaylistVideo
from pod.video.models import Video
from pod.video.models import VideoImageModel
from pod.video.models import EncodingStep

from pod.main.context_processors import TEMPLATE_VISIBLE_SETTINGS

# from fractions import Fraction # use for keyframe
from webvtt import WebVTT, Caption
import logging
import os
import time
import subprocess
import json
import re
import tempfile
import threading

if apps.is_installed('pod.filepicker'):
    try:
        from pod.filepicker.models import CustomImageModel
        from pod.filepicker.models import UserDirectory
    except ImportError:
        pass

FILEPICKER = True if apps.is_installed('pod.filepicker') else False

FFMPEG = getattr(settings, 'FFMPEG', 'ffmpeg')
FFPROBE = getattr(settings, 'FFPROBE', 'ffprobe')
DEBUG = getattr(settings, 'DEBUG', True)

log = logging.getLogger(__name__)

# try to create a new segment every X seconds
SEGMENT_TARGET_DURATION = getattr(settings, 'SEGMENT_TARGET_DURATION', 2)
# maximum accepted bitrate fluctuations
MAX_BITRATE_RATIO = getattr(settings, 'MAX_BITRATE_RATIO', 1.07)
# maximum buffer size between bitrate conformance checks
RATE_MONITOR_BUFFER_RATIO = getattr(
    settings, 'RATE_MONITOR_BUFFER_RATIO', 1.5)
# maximum threads use by ffmpeg
FFMPEG_NB_THREADS = getattr(settings, 'FFMPEG_NB_THREADS', 0)

GET_INFO_VIDEO = getattr(
    settings,
    'GET_INFO_VIDEO',
    "%(ffprobe)s -v quiet -show_format -show_streams -select_streams v:0 "
    + "-print_format json -i %(source)s")

GET_INFO_AUDIO = getattr(
    settings,
    'GET_INFO_AUDIO',
    "%(ffprobe)s -v quiet -show_format -show_streams -select_streams a:0 "
    + "-print_format json -i %(source)s")

FFMPEG_STATIC_PARAMS = getattr(
    settings,
    'FFMPEG_STATIC_PARAMS',
    " -c:a aac -ar 48000 -c:v h264 -profile:v high -pix_fmt yuv420p -crf 20 "
    + "-sc_threshold 0 -force_key_frames \"expr:gte(t,n_forced*1)\" "
    + "-deinterlace -threads %(nb_threads)s ")
# + "-deinterlace -threads %(nb_threads)s -g %(key_frames_interval)s "
# + "-keyint_min %(key_frames_interval)s ")

FFMPEG_MISC_PARAMS = getattr(settings, 'MISC_PARAMS', " -hide_banner -y ")

AUDIO_BITRATE = getattr(settings, 'AUDIO_BITRATE', "192k")

ENCODING_M4A = getattr(
    settings,
    'ENCODING_M4A',
    "%(ffmpeg)s -i %(source)s %(misc_params)s -c:a aac -b:a %(audio_bitrate)s "
    + "-vn -threads %(nb_threads)s "
    + "\"%(output_dir)s/audio_%(audio_bitrate)s.m4a\"")

ENCODE_MP3_CMD = getattr(
    settings, 'ENCODE_MP3_CMD',
    "%(ffmpeg)s -i %(source)s %(misc_params)s -vn -b:a %(audio_bitrate)s "
    + "-vn -f mp3 -threads %(nb_threads)s "
    + "\"%(output_dir)s/audio_%(audio_bitrate)s.mp3\"")

EMAIL_ON_ENCODING_COMPLETION = getattr(
    settings, 'EMAIL_ON_ENCODING_COMPLETION', True)

FILE_UPLOAD_TEMP_DIR = getattr(
    settings, 'FILE_UPLOAD_TEMP_DIR', '/tmp')

TITLE_SITE = getattr(TEMPLATE_VISIBLE_SETTINGS, 'TITLE_SITE', 'Pod')
CONTACT_US_EMAIL = getattr(settings, 'CONTACT_US_EMAIL', [
                           mail for name, mail in getattr(settings, 'ADMINS')])
HELP_MAIL = getattr(settings, 'HELP_MAIL', 'noreply@univ.fr')

# ##########################################################################
# ENCODE VIDEO : THREAD TO LAUNCH ENCODE
# ##########################################################################


def start_encode(video_id):
    log.info("START ENCODE VIDEO ID %s" % video_id)
    t = threading.Thread(target=encode_video,
                         args=[video_id])
    t.setDaemon(True)
    t.start()


# ##########################################################################
# ENCODE VIDEO : GENERIC FUNCTION
# ##########################################################################


def change_encoding_step(video_id, num_step, desc):
    encoding_step, created = EncodingStep.objects.get_or_create(
        video=Video.objects.get(id=video_id))
    encoding_step.num_step = num_step
    encoding_step.desc_step = desc
    encoding_step.save()
    if DEBUG:
        print("step: %d - desc: %s" % (
            num_step, desc
        ))


def add_encoding_log(video_id, log):
    encoding_log, created = EncodingLog.objects.get_or_create(
        video=Video.objects.get(id=video_id))
    if encoding_log.log:
        encoding_log.log += "\n\n%s" % log
    else:
        encoding_log.log = "\n\n%s" % log
    encoding_log.save()
    if DEBUG:
        print(log)


def check_file(path_file):
    if os.access(path_file, os.F_OK) and os.stat(path_file).st_size > 0:
        return True
    return False

# ##########################################################################
# ENCODE VIDEO : MAIN FUNCTION
# ##########################################################################


def encode_video(video_id):
    start = "Start at : %s" % time.ctime()

    change_encoding_step(video_id, 0, "start")
    add_encoding_log(video_id, start)

    video_to_encode = Video.objects.get(id=video_id)
    video_to_encode.encoding_in_progress = True
    video_to_encode.save()

    if check_file(video_to_encode.video.path):

        change_encoding_step(video_id, 1, "remove old data")
        remove_msg = remove_old_data(video_id)
        add_encoding_log(video_id, "remove old data : %s" % remove_msg)

        change_encoding_step(video_id, 2, "get video data")
        video_data = {}
        try:
            video_data = get_video_data(video_id)
            add_encoding_log(video_id, "get video data : %s" %
                             video_data["msg"])
        except ValueError:
            msg = "Error in get video data"
            change_encoding_step(video_id, -1, msg)
            add_encoding_log(video_id, msg)
            send_email(msg, video_id)
            return False

        video_to_encode = Video.objects.get(id=video_id)
        video_to_encode.duration = video_data["duration"]
        video_to_encode.save()

        # create video dir
        change_encoding_step(video_id, 3, "create output dir")
        output_dir = create_outputdir(video_id, video_to_encode.video.path)
        add_encoding_log(video_id, "output_dir : %s" % output_dir)

        if video_data["is_video"]:
            # encodage_video
            # create encoding video command
            change_encoding_step(
                video_id, 4,
                "encoding video file : 1/11 get video command")
            video_command_playlist = get_video_command_playlist(
                video_id,
                video_data,
                output_dir)
            add_encoding_log(
                video_id,
                "video_command_playlist : %s" % video_command_playlist["cmd"])
            video_command_mp4 = get_video_command_mp4(
                video_id,
                video_data,
                output_dir)
            add_encoding_log(
                video_id,
                "video_command_mp4 : %s" % video_command_mp4["cmd"])
            # launch encode video
            change_encoding_step(
                video_id, 4,
                "encoding video file : 2/11 encode_video_playlist")
            msg = encode_video_playlist(
                video_to_encode.video.path,
                video_command_playlist["cmd"],
                output_dir)
            add_encoding_log(
                video_id,
                "encode_video_playlist : %s" % msg)
            change_encoding_step(
                video_id, 4,
                "encoding video file : 3/11 encode_video_mp4")
            msg = encode_video_mp4(
                video_to_encode.video.path,
                video_command_mp4["cmd"],
                output_dir)
            add_encoding_log(
                video_id,
                "encode_video_mp4 : %s" % msg)
            # save playlist files
            change_encoding_step(
                video_id, 4,
                "encoding video file : 4/11 save_playlist_file")
            msg = save_playlist_file(
                video_id,
                video_command_playlist["list_file"],
                output_dir)
            add_encoding_log(
                video_id,
                "save_playlist_file : %s" % msg)
            # save_playlist_master
            change_encoding_step(
                video_id, 4,
                "encoding video file : 5/11 save_playlist_master")
            msg = save_playlist_master(
                video_id,
                output_dir,
                video_command_playlist["master_playlist"])
            add_encoding_log(
                video_id,
                "save_playlist_master : %s" % msg)
            # save mp4 files
            change_encoding_step(
                video_id, 4,
                "encoding video file : 6/11 save_mp4_file")
            msg = save_mp4_file(
                video_id,
                video_command_mp4["list_file"],
                output_dir)
            add_encoding_log(
                video_id,
                "save_mp4_file : %s" % msg)

            # get the lower size of encoding mp4
            ev = EncodingVideo.objects.filter(
                video=video_to_encode, encoding_format="video/mp4")
            if ev.count() == 0:
                msg = "NO MP4 FILES FOUND !"
                add_encoding_log(video_id, msg)
                change_encoding_step(video_id, -1, msg)
                send_email(msg, video_id)
                return
            video_mp4 = sorted(ev, key=lambda m: m.height)[0]

            # create overview
            overviewfilename = '%(output_dir)s/overview.vtt' % {
                'output_dir': output_dir}
            image_url = 'overview.png'
            overviewimagefilename = '%(output_dir)s/%(image_url)s' % {
                'output_dir': output_dir, 'image_url': image_url}
            image_width = video_mp4.width / 4  # width of generate image file
            change_encoding_step(
                video_id, 4,
                "encoding video file : 7/11 remove_previous_overview")
            remove_previous_overview(overviewfilename, overviewimagefilename)
            nb_img = 99 if (
                video_data["duration"] > 99) else video_data["duration"]
            change_encoding_step(
                video_id, 4,
                "encoding video file : 8/11 create_overview_image")
            msg = create_overview_image(
                video_id,
                video_mp4.video.video.path,
                nb_img, image_width, overviewimagefilename)
            add_encoding_log(
                video_id,
                "create_overview_image : %s" % msg)
            change_encoding_step(
                video_id, 4,
                "encoding video file : 9/11 create_overview_vtt")
            overview = ImageFile(open(overviewimagefilename, 'rb'))
            image_height = int(overview.height)
            overview.close()
            image_url = os.path.basename(overviewimagefilename)
            image = {
                'image_width': image_width,
                'image_height': image_height,
                'image_url': image_url
            }
            msg = create_overview_vtt(
                video_id, nb_img, image,
                video_data["duration"], overviewfilename)
            add_encoding_log(
                video_id,
                "create_overview_vtt : %s" % msg)
            change_encoding_step(
                video_id, 4,
                "encoding video file : 10/11 save_overview_vtt")
            msg = save_overview_vtt(video_id, overviewfilename)
            add_encoding_log(
                video_id,
                "save_overview_vtt : %s" % msg)
            # create thumbnail
            change_encoding_step(
                video_id, 4,
                "encoding video file : 11/11 create_and_save_thumbnails")
            msg = create_and_save_thumbnails(
                video_mp4.video.video.path, video_mp4.width, video_id)
            add_encoding_log(
                video_id,
                "create_and_save_thumbnails : %s" % msg)
        else:
            # if file is audio, encoding to m4a for player
            video_to_encode = Video.objects.get(id=video_id)
            video_to_encode.is_video = False
            video_to_encode.save()
            encode_m4a(video_id, video_data["contain_audio"],
                       video_to_encode.video.path, output_dir)

        encode_mp3(video_id, video_data["contain_audio"],
                   video_to_encode.video.path, output_dir)

        # envois mail fin encodage
        if EMAIL_ON_ENCODING_COMPLETION:
            send_email_encoding(video_to_encode)

        change_encoding_step(video_id, 0, "done")

        video_to_encode = Video.objects.get(id=video_id)
        video_to_encode.encoding_in_progress = False
        video_to_encode.save()

    else:
        msg = "Wrong file or path : "\
            + "\n%s" % video_to_encode.video.path
        add_encoding_log(video_id, msg)
        change_encoding_step(video_id, -1, msg)
        send_email(msg, video_id)


# ##########################################################################
# ENCODE VIDEO : GET VIDEO DATA
# ##########################################################################


def get_video_data(video_id):
    video_to_encode = Video.objects.get(id=video_id)
    msg = ""
    source = "%s" % video_to_encode.video.path
    command = GET_INFO_VIDEO % {'ffprobe': FFPROBE, 'source': source}
    ffproberesult = subprocess.getoutput(command)
    msg += "\nffprobe command : %s" % command
    add_encoding_log(
        video_id,
        "command : %s \n ffproberesult : %s" % (command, ffproberesult))
    info = json.loads(ffproberesult)
    msg += "%s" % json.dumps(
        info, sort_keys=True, indent=4, separators=(',', ': '))
    is_video = False
    contain_audio = False
    in_height = 0
    duration = 0
    key_frames_interval = 0
    if len(info["streams"]) > 0:
        is_video = True
        if info["streams"][0].get('height'):
            in_height = info["streams"][0]['height']
        """
        if (info["streams"][0]['avg_frame_rate']
        or info["streams"][0]['r_frame_rate']):
            if info["streams"][0]['avg_frame_rate'] != "0/0":
                # nb img / sec.
                frame_rate = info["streams"][0]['avg_frame_rate']
                key_frames_interval = int(round(Fraction(frame_rate)))
            else:
                frame_rate = info["streams"][0]['r_frame_rate']
                key_frames_interval = int(round(Fraction(frame_rate)))
        """

    # check audio
    command = GET_INFO_AUDIO % {'ffprobe': FFPROBE, 'source': source}
    ffproberesult = subprocess.getoutput(command)
    msg += "\nffprobe command : %s" % command
    add_encoding_log(
        video_id,
        "command : %s \n ffproberesult : %s" % (command, ffproberesult))
    info = json.loads(ffproberesult)
    msg += "%s" % json.dumps(
        info, sort_keys=True, indent=4, separators=(',', ': '))
    if len(info["streams"]) > 0:
        contain_audio = True

    if info["format"].get('duration'):
        duration = int(float("%s" % info["format"]['duration']))

    msg += "\nIN_HEIGHT : %s" % in_height
    msg += "\nKEY FRAMES INTERVAL : %s" % key_frames_interval
    msg += "\nDURATION : %s" % duration
    return {
        'msg': msg,
        'is_video': is_video,
        'contain_audio': contain_audio,
        'in_height': in_height,
        'key_frames_interval': key_frames_interval,
        'duration': duration
    }


# ##########################################################################
# ENCODE VIDEO : CREATE OUTPUT DIR
# ##########################################################################


def create_outputdir(video_id, video_path):
    dirname = os.path.dirname(video_path)
    output_dir = os.path.join(dirname, "%04d" % video_id)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    return output_dir


###############################################################
# MP4
###############################################################


def get_video_command_mp4(video_id, video_data, output_dir):
    in_height = video_data["in_height"]
    renditions = VideoRendition.objects.filter(encode_mp4=True)
    # the lower height in first
    renditions = sorted(renditions, key=lambda m: m.height)
    static_params = FFMPEG_STATIC_PARAMS % {
        'nb_threads': FFMPEG_NB_THREADS,
        'key_frames_interval': video_data["key_frames_interval"]
    }
    list_file = []
    cmd = ""
    for rendition in renditions:
        bitrate = rendition.video_bitrate
        audiorate = rendition.audio_bitrate
        height = rendition.height
        if in_height >= int(height) or rendition == renditions[0]:
            int_bitrate = int(
                re.search("(\d+)k", bitrate, re.I).groups()[0])
            maxrate = int_bitrate * MAX_BITRATE_RATIO
            bufsize = int_bitrate * RATE_MONITOR_BUFFER_RATIO

            name = "%sp" % height

            cmd += " %s -vf " % (static_params,)
            cmd += "\"scale=-2:%s\"" % (height)
            # cmd += "force_original_aspect_ratio=decrease"
            cmd += " -b:v %s -maxrate %sk -bufsize %sk -b:a %s" % (
                bitrate, int(maxrate), int(bufsize), audiorate)

            cmd += " -movflags faststart -write_tmcd 0 \"%s/%s.mp4\"" % (
                output_dir, name)
            list_file.append(
                {"name": name, 'rendition': rendition})
    return {
        'cmd': cmd,
        'list_file': list_file
    }


def encode_video_mp4(source, cmd, output_dir):
    ffmpegMp4Command = "%s %s -i %s %s" % (
        FFMPEG, FFMPEG_MISC_PARAMS, source, cmd)

    msg = "ffmpegMp4Command :\n%s" % ffmpegMp4Command
    msg += "Encoding Mp4 : %s" % time.ctime()

    ffmpegvideo = subprocess.getoutput(ffmpegMp4Command)

    msg += "End Encoding Mp4 : %s" % time.ctime()

    with open(output_dir + "/encoding.log", "a") as f:
        f.write('\n\nffmpegvideoMP4:\n\n')
        f.write(ffmpegvideo)

    return msg


def save_mp4_file(video_id, list_file, output_dir):
    msg = ""
    video_to_encode = Video.objects.get(id=video_id)
    for file in list_file:
        videofilenameMp4 = os.path.join(output_dir, "%s.mp4" % file['name'])
        msg += "\n- videofilenameMp4 :\n%s" % videofilenameMp4
        if check_file(videofilenameMp4):
            encoding, created = EncodingVideo.objects.get_or_create(
                name=file['name'],
                video=video_to_encode,
                rendition=file['rendition'],
                encoding_format="video/mp4")
            encoding.source_file = videofilenameMp4.replace(
                settings.MEDIA_ROOT + '/', '')
            encoding.save()
        else:
            msg = "save_mp4_file Wrong file or path : "\
                + "\n%s " % (videofilenameMp4)
            add_encoding_log(video_id, msg)
            change_encoding_step(video_id, -1, msg)
            send_email(msg, video_id)
    return msg


###############################################################
# AUDIO
###############################################################


def encode_m4a(video_id, contain_audio, source, output_dir):
    msg = ""
    if contain_audio:
        change_encoding_step(
            video_id, 4,
            "encoding audio file")
        msg = encode_video_m4a(
            video_id, source, output_dir)
        add_encoding_log(
            video_id,
            "encode_video_m4a : %s" % msg)
    else:
        msg = "\n%s\nNO VIDEO AND AUDIO FOUND !!!!\n%s\n" % (
            20 * "-", 20 * "-")
        add_encoding_log(video_id, msg)
        change_encoding_step(video_id, -1, msg)
        send_email(msg, video_id)


def encode_mp3(video_id, contain_audio, source, output_dir):
    # encodage_audio_mp3 for all file !
    msg = ""
    if contain_audio:
        change_encoding_step(
            video_id, 4,
            "encoding audio mp3 file")
        msg = encode_video_mp3(
            video_id, source, output_dir)
        add_encoding_log(
            video_id,
            "encode_video_mp3 : %s" % msg)
    else:
        msg = "No stream audio found"
        add_encoding_log(
            video_id,
            "encode_video_mp3 : %s" % msg)


def encode_video_m4a(video_id, source, output_dir):
    command = ENCODING_M4A % {
        'ffmpeg': FFMPEG,
        'source': source,
        'misc_params': FFMPEG_MISC_PARAMS,
        'nb_threads': FFMPEG_NB_THREADS,
        'output_dir': output_dir,
        'audio_bitrate': AUDIO_BITRATE
    }
    msg = "\nffmpegM4aCommand :\n%s" % command
    msg += "\n- Encoding M4A : %s" % time.ctime()
    ffmpegaudio = subprocess.getoutput(command)
    msg += "\n- End Encoding Mp4 : %s" % time.ctime()

    audiofilename = output_dir + "/audio_%s.m4a" % AUDIO_BITRATE
    video_to_encode = Video.objects.get(id=video_id)
    if check_file(audiofilename):
        encoding, created = EncodingAudio.objects.get_or_create(
            name="audio",
            video=video_to_encode,
            encoding_format="video/mp4")
        encoding.source_file = output_dir.replace(
            settings.MEDIA_ROOT + '/', '')
        + "/audio_%s.m4a" % AUDIO_BITRATE
        encoding.save()
        msg += "\n- encode_video_m4a :\n%s" % audiofilename
    else:
        msg += "\n- encode_video_m4a Wrong file or path %s " % audiofilename
        add_encoding_log(video_id, msg)
        change_encoding_step(video_id, -1, msg)
        send_email(msg, video_id)

    with open(output_dir + "/encoding.log", "a") as f:
        f.write('\n\nffmpegaudio:\n\n')
        f.write(ffmpegaudio)

    return msg


def encode_video_mp3(video_id, source, output_dir):
    msg = "\nEncoding MP3 : %s" % time.ctime()
    command = ENCODE_MP3_CMD % {
        'ffmpeg': FFMPEG,
        'source': source,
        'misc_params': FFMPEG_MISC_PARAMS,
        'nb_threads': FFMPEG_NB_THREADS,
        'output_dir': output_dir,
        'audio_bitrate': AUDIO_BITRATE
    }
    msg = "ffmpegMP3Command :\n%s" % command
    msg += "\n- Encoding MP3 : %s" % time.ctime()
    ffmpegaudio = subprocess.getoutput(command)
    msg += "\n- End Encoding MP3 : %s" % time.ctime()

    audiofilename = output_dir + "/audio_%s.mp3" % AUDIO_BITRATE
    video_to_encode = Video.objects.get(id=video_id)
    if check_file(audiofilename):
        encoding, created = EncodingAudio.objects.get_or_create(
            name="audio",
            video=video_to_encode,
            encoding_format="audio/mp3")
        encoding.source_file = audiofilename.replace(
            settings.MEDIA_ROOT + '/', '')
        encoding.save()
        msg += "\n- encode_video_mp3 :\n%s" % audiofilename
    else:
        msg += "\n- encode_video_mp3 Wrong file or path %s " % audiofilename
        add_encoding_log(video_id, msg)
        change_encoding_step(video_id, -1, msg)
        send_email(msg, video_id)

    with open(output_dir + "/encoding.log", "a") as f:
        f.write('\n\nffmpegaudio:\n\n')
        f.write(ffmpegaudio)

    return msg


###############################################################
# PLAYLIST
###############################################################


def get_video_command_playlist(video_id, video_data, output_dir):
    in_height = video_data["in_height"]
    master_playlist = "#EXTM3U\n#EXT-X-VERSION:3\n"
    static_params = FFMPEG_STATIC_PARAMS % {
        'nb_threads': FFMPEG_NB_THREADS,
        'key_frames_interval': video_data["key_frames_interval"]
    }
    list_file = []
    cmd = ""
    renditions = VideoRendition.objects.all()
    # the lower height in first
    renditions = sorted(renditions, key=lambda m: m.height)
    for rendition in renditions:
        resolution = rendition.resolution
        bitrate = rendition.video_bitrate
        audiorate = rendition.audio_bitrate
        height = rendition.height
        if in_height >= int(height) or rendition == renditions[0]:
            int_bitrate = int(
                re.search("(\d+)k", bitrate, re.I).groups()[0])
            maxrate = int_bitrate * MAX_BITRATE_RATIO
            bufsize = int_bitrate * RATE_MONITOR_BUFFER_RATIO
            bandwidth = int_bitrate * 1000

            name = "%sp" % height

            cmd += " %s -vf " % (static_params,)
            cmd += "\"scale=-2:%s\"" % (height)
            # cmd += "scale=w=%s:h=%s:" % (width, height)
            # cmd += "force_original_aspect_ratio=decrease"
            cmd += " -b:v %s -maxrate %sk -bufsize %sk -b:a %s" % (
                bitrate, int(maxrate), int(bufsize), audiorate)
            cmd += " -hls_playlist_type vod -hls_time %s \
                -hls_flags single_file %s/%s.m3u8" % (
                SEGMENT_TARGET_DURATION, output_dir, name)
            list_file.append(
                {"name": name, 'rendition': rendition})
            master_playlist += "#EXT-X-STREAM-INF:BANDWIDTH=%s,\
                RESOLUTION=%s\n%s.m3u8\n" % (
                bandwidth, resolution, name)
    return {
        'cmd': cmd,
        'list_file': list_file,
        'master_playlist': master_playlist
    }


def encode_video_playlist(source, cmd, output_dir):

    ffmpegPlaylistCommand = "%s %s -i %s %s" % (
        FFMPEG, FFMPEG_MISC_PARAMS, source, cmd)

    msg = "ffmpegPlaylistCommand :\n%s" % ffmpegPlaylistCommand
    msg += "Encoding Playlist : %s" % time.ctime()

    ffmpegvideo = subprocess.getoutput(ffmpegPlaylistCommand)

    msg += "End Encoding Playlist : %s" % time.ctime()

    with open(output_dir + "/encoding.log", "a") as f:
        f.write('\n\nffmpegvideoPlaylist:\n\n')
        f.write(ffmpegvideo)

    return msg


def save_playlist_file(video_id, list_file, output_dir):
    msg = ""
    video_to_encode = Video.objects.get(id=video_id)
    for file in list_file:
        videofilenameM3u8 = os.path.join(output_dir, "%s.m3u8" % file['name'])
        videofilenameTS = os.path.join(output_dir, "%s.ts" % file['name'])
        msg += "\n- videofilenameM3u8 :\n%s" % videofilenameM3u8
        msg += "\n- videofilenameTS :\n%s" % videofilenameTS

        if check_file(videofilenameM3u8) and check_file(videofilenameTS):

            encoding, created = EncodingVideo.objects.get_or_create(
                name=file['name'],
                video=video_to_encode,
                rendition=file['rendition'],
                encoding_format="video/mp2t")
            encoding.source_file = videofilenameTS.replace(
                settings.MEDIA_ROOT + '/', '')
            encoding.save()

            playlist, created = PlaylistVideo.objects.get_or_create(
                name=file['name'],
                video=video_to_encode,
                encoding_format="application/x-mpegURL")
            playlist.source_file = videofilenameM3u8.replace(
                settings.MEDIA_ROOT + '/', '')
            playlist.save()
        else:
            msg = "save_playlist_file Wrong file or path : "\
                + "\n%s and %s" % (videofilenameM3u8, videofilenameTS)
            add_encoding_log(video_id, msg)
            change_encoding_step(video_id, -1, msg)
            send_email(msg, video_id)
    return msg


def save_playlist_master(video_id, output_dir, master_playlist):
    msg = ""
    playlist_master_file = output_dir + "/playlist.m3u8"
    video_to_encode = Video.objects.get(id=video_id)
    with open(playlist_master_file, "w") as f:
        f.write(master_playlist)
    if check_file(playlist_master_file):
        playlist, created = PlaylistVideo.objects.get_or_create(
            name="playlist",
            video=video_to_encode,
            encoding_format="application/x-mpegURL")
        playlist.source_file = output_dir.replace(
            settings.MEDIA_ROOT + '/', '') + "/playlist.m3u8"
        playlist.save()

        msg += "\n- Playlist :\n%s" % playlist_master_file
    else:
        msg = "save_playlist_master Wrong file or path : "\
            + "\n%s" % playlist_master_file
        add_encoding_log(video_id, msg)
        change_encoding_step(video_id, -1, msg)
        send_email(msg, video_id)
    return msg


###############################################################
# OVERVIEW
###############################################################


def remove_previous_overview(overviewfilename, overviewimagefilename):
    if os.path.isfile(overviewimagefilename):
        os.remove(overviewimagefilename)
    if os.path.isfile(overviewfilename):
        os.remove(overviewfilename)


def create_overview_image(
    video_id, source, nb_img, image_width, overviewimagefilename
):
    msg = "\ncreate overview image file"

    for i in range(0, nb_img):
        stamp = "%s" % i
        if nb_img == 99:
            stamp += "%"
        else:
            stamp = time.strftime('%H:%M:%S', time.gmtime(i))
        cmd_ffmpegthumbnailer = "ffmpegthumbnailer -t \"%(stamp)s\" \
        -s \"%(image_width)s\" -i %(source)s -c png \
        -o %(overviewimagefilename)s_strip%(num)s.png" % {
            "stamp": stamp,
            'source': source,
            'num': i,
            'overviewimagefilename': overviewimagefilename,
            'image_width': image_width
        }
        subprocess.getoutput(cmd_ffmpegthumbnailer)
        cmd_montage = "montage -geometry +0+0 %(overviewimagefilename)s \
        %(overviewimagefilename)s_strip%(num)s.png \
        %(overviewimagefilename)s" % {
            'overviewimagefilename': overviewimagefilename,
            'num': i
        }
        subprocess.getoutput(cmd_montage)
        if os.path.isfile("%(overviewimagefilename)s_strip%(num)s.png" % {
            'overviewimagefilename': overviewimagefilename,
            'num': i
        }):
            os.remove("%(overviewimagefilename)s_strip%(num)s.png" %
                      {'overviewimagefilename': overviewimagefilename,
                       'num': i})
    if check_file(overviewimagefilename):
        msg += "\n- overviewimagefilename :\n%s" % overviewimagefilename
    else:
        msg = "overviewimagefilename Wrong file or path : "\
            + "\n%s" % overviewimagefilename
        add_encoding_log(video_id, msg)
        change_encoding_step(video_id, -1, msg)
        send_email(msg, video_id)


def create_overview_vtt(video_id, nb_img, image, duration, overviewfilename):
    msg = "\ncreate overview vtt file"
    image_width = image["image_width"]
    image_height = image["image_height"]
    image_url = image["image_url"]
    # creating webvtt file
    webvtt = WebVTT()
    for i in range(0, nb_img):
        if nb_img == 99:
            start = format(float(duration * i / 100), '.3f')
            end = format(float(duration * (i + 1) / 100), '.3f')
        else:
            start = format(float(i), '.3f')
            end = format(float(i + 1), '.3f')

        start_time = time.strftime(
            '%H:%M:%S',
            time.gmtime(int(str(start).split('.')[0]))
        )
        start_time += ".%s" % (str(start).split('.')[1])
        end_time = time.strftime('%H:%M:%S', time.gmtime(
            int(str(end).split('.')[0]))) + ".%s" % (str(end).split('.')[1])
        caption = Caption(
            '%s' % start_time,
            '%s' % end_time,
            '%s#xywh=%d,%d,%d,%d' % (
                image_url, image_width * i, 0, image_width, image_height)
        )
        webvtt.captions.append(caption)
    webvtt.save(overviewfilename)
    if check_file(overviewfilename):
        msg += "\n- overviewfilename :\n%s" % overviewfilename
    else:
        msg = "overviewfilename Wrong file or path : "\
            + "\n%s" % overviewfilename
        add_encoding_log(video_id, msg)
        change_encoding_step(video_id, -1, msg)
        send_email(msg, video_id)
    return msg


def save_overview_vtt(video_id, overviewfilename):
    msg = "\nstore vtt file in bdd with video model overview field"
    if check_file(overviewfilename):
        # save file in bdd
        video_to_encode = Video.objects.get(id=video_id)
        video_to_encode.overview = overviewfilename.replace(
            settings.MEDIA_ROOT + '/', '')
        video_to_encode.save()
        msg += "\n- save_overview_vtt :\n%s" % overviewfilename
    else:
        msg += "\nERROR OVERVIEW %s Output size is 0" % overviewfilename
        add_encoding_log(video_id, msg)
        change_encoding_step(video_id, -1, msg)
        send_email(msg, video_id)
    return msg


###############################################################
# THUMBNAILS
###############################################################


def create_and_save_thumbnails(source, image_width, video_id):
    msg = "\nCREATE AND SAVE THUMBNAILS : %s" % time.ctime()
    tempimgfile = tempfile.NamedTemporaryFile(
        dir=FILE_UPLOAD_TEMP_DIR, suffix='')
    for i in range(0, 3):
        percent = str((i + 1) * 25) + "%"
        cmd_ffmpegthumbnailer = "ffmpegthumbnailer -t \"%(percent)s\" \
        -s \"%(image_width)s\" -i %(source)s -c png \
        -o %(tempfile)s_%(num)s.png" % {
            "percent": percent,
            'source': source,
            'num': i,
            'image_width': image_width,
            'tempfile': tempimgfile.name
        }
        subprocess.getoutput(cmd_ffmpegthumbnailer)
        thumbnailfilename = "%(tempfile)s_%(num)s.png" % {
            'num': i,
            'tempfile': tempimgfile.name
        }
        if check_file(thumbnailfilename):
            if FILEPICKER:
                video_to_encode = Video.objects.get(id=video_id)
                homedir, created = UserDirectory.objects.get_or_create(
                    name='Home',
                    owner=video_to_encode.owner,
                    parent=None)
                videodir, created = UserDirectory.objects.get_or_create(
                    name='%s' % video_to_encode.slug,
                    owner=video_to_encode.owner,
                    parent=homedir)
                thumbnail = CustomImageModel(
                    directory=videodir,
                    created_by=video_to_encode.owner
                )
                thumbnail.file.save(
                    "%s_%s.png" % (video_to_encode.slug, i),
                    File(open(thumbnailfilename, "rb")),
                    save=True)
                thumbnail.save()
                if i == 0:
                    video_to_encode = Video.objects.get(id=video_id)
                    video_to_encode.thumbnail = thumbnail
                    video_to_encode.save()
            else:
                thumbnail = VideoImageModel()
                thumbnail.file.save(
                    "%d_%s.png" % (video_id, i),
                    File(open(thumbnailfilename, "rb")),
                    save=True)
                thumbnail.save()
                if i == 0:
                    video_to_encode = Video.objects.get(id=video_id)
                    video_to_encode.thumbnail = thumbnail
                    video_to_encode.save()
            # remove tempfile
            msg += "\n- thumbnailfilename %s :\n%s" % (i, thumbnail.file.path)
            os.remove(thumbnailfilename)
        else:
            msg += "\nERROR THUMBNAILS %s " % thumbnailfilename
            msg += "Wrong file or path"
            add_encoding_log(video_id, msg)
            change_encoding_step(video_id, -1, msg)
            send_email(msg, video_id)
    return msg


###############################################################
# REMOVE ENCODING
###############################################################


def remove_old_data(video_id):
    video_to_encode = Video.objects.get(id=video_id)
    video_to_encode.thumbnail = None
    if video_to_encode.overview:
        image_overview = os.path.join(
            os.path.dirname(video_to_encode.overview.path), 'overview.png')
        if os.path.isfile(image_overview):
            os.remove(image_overview)
        video_to_encode.overview.delete()
    video_to_encode.overview = None
    video_to_encode.save()

    encoding_log, created = EncodingLog.objects.get_or_create(
        video=Video.objects.get(id=video_id))
    encoding_log.log = ""
    encoding_log.save()

    encoding_log_msg = ""
    encoding_log_msg += remove_previous_encoding_video(
        video_to_encode)
    encoding_log_msg += remove_previous_encoding_audio(
        video_to_encode)
    encoding_log_msg += remove_previous_encoding_playlist(
        video_to_encode)
    return encoding_log_msg


def remove_previous_encoding_video(video_to_encode):
    msg = "\n"
    # Remove previous encoding Video
    previous_encoding_video = EncodingVideo.objects.filter(
        video=video_to_encode)
    if len(previous_encoding_video) > 0:
        msg += "\nDELETE PREVIOUS ENCODING VIDEO"
        # previous_encoding.delete()
        for encoding in previous_encoding_video:
            encoding.delete()
    else:
        msg += "Video : Nothing to delete"
    return msg


def remove_previous_encoding_audio(video_to_encode):
    msg = "\n"
    # Remove previous encoding Audio
    previous_encoding_audio = EncodingAudio.objects.filter(
        video=video_to_encode)
    if len(previous_encoding_audio) > 0:
        msg += "\nDELETE PREVIOUS ENCODING AUDIO"
        # previous_encoding.delete()
        for encoding in previous_encoding_audio:
            encoding.delete()
    else:
        msg += "Audio : Nothing to delete"
    return msg


def remove_previous_encoding_playlist(video_to_encode):
    msg = "\n"
    # Remove previous encoding Playlist
    previous_playlist = PlaylistVideo.objects.filter(video=video_to_encode)
    if len(previous_playlist) > 0:
        msg += "DELETE PREVIOUS PLAYLIST M3U8"
        # previous_encoding.delete()
        for encoding in previous_playlist:
            encoding.delete()
    else:
        msg += "Playlist : Nothing to delete"
    return msg


###############################################################
# EMAIL
###############################################################


def send_email(msg, video_id):
    subject = "[" + TITLE_SITE + \
        "] Error Encoding Video id:%s" % video_id
    message = "Error Encoding  video id : %s\n%s" % (
        video_id, msg)
    html_message = "<p>Error Encoding video id : %s</p><p>%s</p>" % (
        video_id,
        msg.replace('\n', "<br/>"))
    mail_admins(
        subject,
        message,
        fail_silently=False,
        html_message=html_message)


def send_email_encoding(video_to_encode):
    if DEBUG:
        print("SEND EMAIL ON ENCODING COMPLETION")
    content_url = "http:%s" % video_to_encode.get_full_url()
    subject = "[%s] %s" % (
        TITLE_SITE,
        _(u"Encoding #%(content_id)s completed") % {
            'content_id': video_to_encode.id
        }
    )
    message = "%s\n\n%s\n%s\n" % (
        _(u"The content “%(content_title)s” has been encoded to Web "
            + "formats, and is now available on %(site_title)s.") % {
            'content_title': video_to_encode.title,
            'site_title': TITLE_SITE
        },
        _(u"You will find it here:"),
        content_url
    )
    from_email = HELP_MAIL
    to_email = []
    to_email.append(video_to_encode.owner.email)
    html_message = ""

    html_message = '<p>%s</p><p>%s<br><a href="%s"><i>%s</i></a>\
                </p>' % (
        _(u"The content “%(content_title)s” has been encoded to Web "
            + "formats, and is now available on %(site_title)s.") % {
            'content_title': '<b>%s</b>' % video_to_encode.title,
            'site_title': TITLE_SITE
        },
        _(u"You will find it here:"),
        content_url,
        content_url
    )
    send_mail(
        subject,
        message,
        from_email,
        to_email,
        fail_silently=False,
        html_message=html_message,
    )
    mail_managers(
        subject, message, fail_silently=False,
        html_message=html_message)