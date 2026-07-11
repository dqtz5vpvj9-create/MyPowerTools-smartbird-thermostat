import yaml
import os

yaml_file_path = os.path.join(os.path.dirname(__file__), 'common_app_info.yaml')

with open(yaml_file_path, 'r') as yaml_file:
    data = yaml.safe_load(yaml_file)
package_default_activities = data['package_default_activities']


package_to_app_name = {
    "com.zhiliaoapp.musically": "TikTok",
    "com.google.android.apps.maps": "Google_Maps",
    "com.iqiyi.i18n": "iQIYI",
    "com.twitter.android": "Twitter",
    "com.spotify.music": "Spotify",
    "com.einnovation.temu": "Temu",
    "com.moji.mjweather": "Moji_Weather",
    "com.tencent.android.qqdownloader": "QQ_Downloder",
    "com.baidu.netdisk": "Baidu_Netdisk",
    "com.tencent.qqmusic": "QQ_Music",
    "com.jingyao.easybike": "Hellobike",
    "com.tencent.androidqqmail": "QQ_Mail",
    'com.Slack': "Slack",
    'com.smile.gifmaker': 'KuaiShou'
}

comm_to_package = {
    ".easybike:tool": "com.jingyao.easybike",
    ".moji.mjweathe": "com.moji.mjweather",
    ".musically:pus": "com.zhiliaoapp.musically",
    "QQPlayerServic": "com.tencent.qqmusic",
    "aoapp.musicall": "com.zhiliaoapp.musically",
    "com.iqiyi.i18n": "com.iqiyi.i18n",
    "downloader:liv": "com.tencent.android.qqdownloader",
    "droid.apps.map": "com.google.android.apps.maps",
    "her:pushservic": "com.moji.mjweather",
    "i18n:downloade": "com.iqiyi.i18n",
    "id.qqdownloade": "com.tencent.android.qqdownloader",
    "ike:pushservic": "com.jingyao.easybike",
    "ingyao.easybik": "com.jingyao.easybike",
    "innovation.tem": "com.einnovation.temu",
    "jweather:mjski": "com.moji.mjweather",
    "m.baidu.netdis": "com.baidu.netdisk",
    "m.spotify.musi": "com.spotify.music",
    "ownloader:clou": "com.tencent.android.qqdownloader",
    "roidqqmail:Pus": "com.tencent.androidqqmail",
    "sybike:pushcor": "com.jingyao.easybike",
    "t.androidqqmai": "com.tencent.androidqqmail",
    "tencent.qqmusi": "com.tencent.qqmusic",
    "tion.temu:tita": "com.einnovation.temu",
    "twitter.androi": "com.twitter.android",
    "wnloader:daemo": "com.tencent.android.qqdownloader",
    'com.Slack': 'com.Slack',
    'ifmaker:push_v': 'com.smile.gifmaker',
    '.smile.gifmake': 'com.smile.gifmaker',
    'aker:messagesd': 'com.smile.gifmaker',
}

def normalize_comm(old_comm):
    valid_comms = list(comm_to_package.keys())
    for idx, valid_comm in enumerate(valid_comms):
        if (valid_comm in old_comm) or (old_comm in valid_comm):
            return valid_comm
    return old_comm