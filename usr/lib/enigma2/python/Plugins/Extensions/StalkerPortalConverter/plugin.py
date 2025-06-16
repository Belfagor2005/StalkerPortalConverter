# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

"""
#########################################################
#                                                       #
#  Stalker Portal Converter Plugin                      #
#  Version: 1.3                                         #
#  Created by Lululla (https://github.com/Belfagor2005) #
#  License: CC BY-NC-SA 4.0                             #
#  https://creativecommons.org/licenses/by-nc-sa/4.0    #
#  Last Modified: "21:50 - 20250606"                    #
#                                                       #
#  Credits:                                             #
#  - Original concept by Lululla                        #
#  Usage of this code without proper attribution        #
#  is strictly prohibited.                              #
#  For modifications and redistribution,                #
#  please maintain this credit header.                  #
#########################################################
"""
__author__ = "Lululla"

import threading
import time
import random
import unicodedata
from enigma import eDVBDB, eTimer
from hashlib import md5
from json import JSONDecodeError, dumps, loads
from os import (
    chmod,
    listdir,
    makedirs,
    remove,
    statvfs,
    replace
)
from os.path import (
    basename,
    dirname,
    exists,
    getsize,
    isdir,
    isfile,
    join,
)
from re import IGNORECASE, compile, search, sub
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Thread
from urllib.parse import urlencode, urlparse  # , quote
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from requests.exceptions import RequestException, SSLError
import ssl

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from Components.config import config, configfile, ConfigSelection, ConfigSubsection, ConfigText

from Plugins.Plugin import PluginDescriptor

from Screens.ChoiceBox import ChoiceBox
from Screens.Console import Console
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from Screens.VirtualKeyBoard import VirtualKeyBoard
from Tools.Directories import defaultRecordingLocation

from . import (
    _,
    b64decoder,
    check_version,
    fetch_system_timezone,
    get_mounted_devices,
    has_enough_free_space,
    installer_url,
    Request,
    requests,
    urlopen,
    cleanName,
    # write_debug_line,
)

currversion = '1.3'

"""Use mode:
playlist.txt with case sensitive
Examples of supported formats:
Standard format:

Panel: http://example.com:80/c/
MAC: 00:1A:79:XX:XX:XX
Compact format:

http://example.com/c/ # My Portal
00:1A:79:XX:XX:XX
Multiple MACs per portal:

Portal: http://server.com:8080/c
MAC1: 00:1A:79:AA:AA:AA
MAC2: 00:1A:79:BB:BB:BB
Unlabeled MAC:

Panel http://example.com/c
00:1A:79:XX:XX:XX
Portal without explicit MAC:

http://server1.com/c/
http://server2.com/c/ # Uses same MAC as server1
00:1A:79:XX:XX:XX
"""


# Ensure movie path
def defaultMoviePath():
    result = config.usage.default_path.value
    if not result.endswith("/"):
        result += "/"
    if not isdir(result):
        return defaultRecordingLocation(config.usage.default_path.value)
    return result


def update_mounts():
    """Update the list of mounted devices and update config choices"""
    mounts = get_mounted_devices()
    if not mounts:
        default_path = defaultMoviePath()
        mounts = [(default_path, default_path)]
    config.plugins.stalkerportal.output_dir.setChoices(mounts, default=mounts[0][0])
    config.plugins.stalkerportal.output_dir.save()


# Configuration setup
config.plugins.stalkerportal = ConfigSubsection()
default_dir = config.movielist.last_videodir.value if isdir(config.movielist.last_videodir.value) else defaultMoviePath()
config.plugins.stalkerportal.output_dir = ConfigSelection(default=default_dir, choices=[])
config.plugins.stalkerportal.type_convert = ConfigSelection(
    default="0",
    choices=[
        ("0", "MAC to M3U"),
        ("1", "MAC to .tv")
    ]
)
config.plugins.stalkerportal.bouquet_position = ConfigSelection(
    default="bottom",
    choices=[("top", _("Top")), ("bottom", _("Bottom"))]
)
config.plugins.stalkerportal.portal_url = ConfigText(default="http://my.server.xyz:8080/c/", fixed_size=False)
config.plugins.stalkerportal.mac_address = ConfigText(default="00:1A:79:00:00:00", fixed_size=False)
AgentRequest = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.3'
update_mounts()


def reload_services():
    """Reload the list of services"""
    eDVBDB.getInstance().reloadServicelist()
    eDVBDB.getInstance().reloadBouquets()


"""
/tmp/account_debug.log
/tmp/stalker_convert_info.log
/tmp/stalker_convert.log
"""
for path in ["/tmp/account_debug.log", "/tmp/stalker_convert_info.log", "/tmp/stalker_convert.log"]:
    if exists(path):
        try:
            remove(path)
        except Exception as e:
            print("[DEBUG] Failed to remove {}: {}".format(path, e))


# issue on this version: Max retries exceeded with url


class StalkerPortalConverter(Screen):
    skin = """
        <screen name="StalkerPortalConverter" position="center,center" size="1280,720" title="Stalker Portal Converter" backgroundColor="#16000000">
            <widget name="title" position="10,10" size="1260,40" font="Regular;30" halign="center" foregroundColor="#00ffff" />

            <!-- Label -->
            <widget name="portal_label" position="10,60" size="300,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
            <widget name="portal_input" position="320,60" size="950,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
            <widget name="mac_label" position="10,100" size="300,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
            <widget name="mac_input" position="320,100" size="950,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
            <widget name="file_label" position="10,140" size="300,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
            <widget name="file_input" position="320,140" size="950,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />

            <!-- Label Extended -->
            <widget name="user_label" position="10,505" size="200,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
            <widget name="user_value" position="220,505" size="300,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
            <widget name="pass_label" position="540,505" size="200,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
            <widget name="pass_value" position="750,505" size="300,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
            <widget name="expiry_label" position="10,535" size="200,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
            <widget name="expiry_value" position="220,535" size="300,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
            <widget name="status_label" position="540,535" size="200,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
            <widget name="status_value" position="750,535" size="300,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
            <widget name="active_label" position="10,565" size="200,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
            <widget name="active_value" position="220,565" size="300,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
            <widget name="max_label" position="540,565" size="200,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
            <widget name="max_value" position="750,565" size="300,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
            <widget name="status" position="9,602" size="1260,59" font="Regular;26" foregroundColor="#00ff00" halign="center" zPosition="2" />

            <!-- List -->
            <widget name="portal_list_label" position="10,180" size="1260,30" font="Regular;24" foregroundColor="#ffff00" zPosition="2"/>
            <widget name="file_list" position="10,220" size="1260,276" scrollbarMode="showOnDemand" itemHeight="40" font="Regular;28" backgroundColor="#252525" zPosition="2" />

            <!-- Buttons -->
            <ePixmap position="10,670" pixmap="skin_default/buttons/red.png" size="30,30" alphatest="blend" zPosition="2" />
            <widget name="key_red" font="Regular;28" position="50,670" size="200,30" halign="left" backgroundColor="black" zPosition="1" transparent="1" />
            <ePixmap position="270,670" pixmap="skin_default/buttons/green.png" size="30,30" alphatest="blend" zPosition="2" />
            <widget name="key_green" font="Regular;28" position="310,670" size="200,30" halign="left" backgroundColor="black" zPosition="2" transparent="1" />
            <ePixmap position="540,670" pixmap="skin_default/buttons/yellow.png" size="30,30" alphatest="blend" zPosition="2" />
            <widget name="key_yellow" font="Regular;28" position="580,670" size="200,30" halign="left" backgroundColor="black" zPosition="2" transparent="1" />
            <ePixmap position="810,670" pixmap="skin_default/buttons/blue.png" size="30,30" alphatest="blend" zPosition="2" />
            <widget name="key_blue" font="Regular;28" position="850,670" size="200,30" halign="left" backgroundColor="black" zPosition="2" transparent="1" />
            <eLabel name="" position="1067,660" size="52,52" backgroundColor="#00ffff" foregroundColor="#000000" halign="center" valign="center" transparent="0" cornerRadius="26" font="Regular; 17" zPosition="1" text="OK" />
            <eLabel name="" position="1130,660" size="52,52" backgroundColor="#00ffff" foregroundColor="#000000" halign="center" valign="center" transparent="0" cornerRadius="26" font="Regular; 17" zPosition="1" text="INFO" />
            <eLabel name="" position="1200,660" size="52,52" backgroundColor="#00ffff" foregroundColor="#000000" halign="center" valign="center" transparent="0" cornerRadius="26" font="Regular; 17" zPosition="1" text="EXIT" />

        </screen>"""

    def __init__(self, session):
        Screen.__init__(self, session)
        self.session = session

        self.portal_list = []
        self.playlist_file = ""
        self.channels = []

        self.account_info_timer = None
        self.account_info_timeout = 15

        self.conversion_running = False
        self.conversion_stopped = False

        self["title"] = Label(_("Stalker Portal to M3U Converter v.%s") % currversion)
        self["portal_label"] = Label(_("Portal URL:"))
        self["portal_input"] = Label(config.plugins.stalkerportal.portal_url.value)
        self["mac_label"] = Label(_("MAC Address:"))
        self["mac_input"] = Label(config.plugins.stalkerportal.mac_address.value)
        self["file_label"] = Label(_("Output File:"))
        self["file_input"] = Label(self.get_output_filename())
        self["portal_list_label"] = Label(_("Valid Portals from Selected File:"))

        # Add new info labels
        self["account_info"] = Label("")
        self["user_label"] = Label(_("User:"))
        self["user_value"] = Label("")
        self["pass_label"] = Label(_("Password:"))
        self["pass_value"] = Label("")
        self["expiry_label"] = Label(_("Expiry:"))
        self["expiry_value"] = Label("")
        self["status_label"] = Label(_("Status:"))
        self["status_value"] = Label("")
        self["active_label"] = Label(_("Active:"))
        self["active_value"] = Label("")
        self["max_label"] = Label(_("Max: "))
        self["max_value"] = Label("")

        self["file_list"] = MenuList([])
        self["status"] = Label(_("Ready - Select a file or enter URL/MAC"))
        self["key_red"] = Label(_("Clear"))
        self["key_green"] = Label(self.get_convert_label())
        self["key_yellow"] = Label(_("Select Playlist"))
        self["key_blue"] = Label(_("Edit"))

        self["actions"] = ActionMap(
            ["StalkerPortalConverter"],
            {
                "cancel": self.close,
                "exit": self.close,
                "info": self.show_info,
                "red": self.clear_fields,
                "green": self.convert,
                "yellow": self.select_playlist_file,
                "blue": self.edit_settings,
                "ok": self.select_portal,
                "up": self.keyUp,
                "down": self.keyDown,
                "left": self.keyLeft,
                "right": self.keyRight,
                # "showVK": self.edit_settings,
            }, -1
        )

        try:
            portal = config.plugins.stalkerportal.portal_url.value
            mac = config.plugins.stalkerportal.mac_address.value
            entry = self.validate_and_add_entry(portal, mac)
            if entry:
                self.portal_list.append(entry)
            display_list = [entry[0] for entry in self.portal_list]
            self["file_list"].setList(display_list)
        except Exception as e:
            print(e)

        # timer stopping conversion
        # Create timers
        self.stop_timer = eTimer()
        self.stop_timer.timeout.get().append(self.reset_conversion_state)
        self.result_timer = eTimer()
        self.result_timer.timeout.get().append(self.handle_conversion_result)
        # self.stop_timer = eTimer()
        # self.stop_timer_conn = self.stop_timer.timeout.get().append(self.reset_conversion_state)
        # self.result_timer = eTimer()
        # self.result_timer_conn = self.result_timer.timeout.get().append(self.handle_conversion_result)

        self.onLayoutFinish.append(self.select_portal)

    def select_portal(self):
        """Select a portal and start async info retrieval"""
        # Stop any ongoing conversion first
        if self.conversion_running:
            self.conversion_stopped = True
            self["status"].setText(_("Stopping current process to switch portal..."))

            # Wait briefly for thread to terminate
            start_time = time.time()
            while self.conversion_running and (time.time() - start_time) < 2.0:
                time.sleep(0.1)

        # Reset conversion state
        self.conversion_running = False
        self.conversion_stopped = False

        selection = self["file_list"].getCurrent()
        if selection and self.portal_list:
            idx = self["file_list"].getSelectedIndex()
            if idx < len(self.portal_list):
                display, portal, mac = self.portal_list[idx]
                config.plugins.stalkerportal.portal_url.value = portal
                config.plugins.stalkerportal.mac_address.value = mac

                # Save the new config values
                configfile.save()
                configfile.load()

                self["portal_input"].setText(portal)
                self["mac_input"].setText(mac)
                self["file_input"].setText(self.get_output_filename())
                self["status"].setText(_("Selected: ") + basename(self.playlist_file))

                # Clear previous info
                self["account_info"].setText("")
                self["user_value"].setText("")
                self["pass_value"].setText("")
                self["expiry_value"].setText("")
                self["status_value"].setText("")
                self["active_value"].setText("")
                self["max_value"].setText("")

                self["status"].setText(_("Loading account info in background..."))
                self.extract_account_info_async()

                # UPDATE THE GREEN BUTTON LABEL
                self["key_green"].setText(self.get_convert_label())

    def extract_account_info_async(self):
        """Run extract_account_info in a background thread and update UI fields"""

        def worker():
            # Show loading state
            self.update_account_fields("", "", "", "", "", "")

            # Retrieve account info
            info = self.extract_account_info()

            if info:
                # Update UI fields
                self.update_account_fields(
                    info.get("user", "N/A"),
                    info.get("password", "N/A"),
                    info.get("expiry", "N/A"),
                    info.get("status", "N/A"),
                    info.get("active", "N/A"),
                    info.get("max", "N/A")
                )
                self["status"].setText(_("Account info loaded"))
            else:
                self["status"].setText(_("Failed to load account info"))

        # Start the worker thread
        threading.Thread(target=worker, daemon=True).start()

    def extract_account_info(self):
        """Extract account information from portal and MAC configuration"""
        try:
            portal = config.plugins.stalkerportal.portal_url.value.strip()
            mac = config.plugins.stalkerportal.mac_address.value.strip()
        except Exception as e:
            self["status"].setText(_("Error accessing configuration values: ") + str(e))
            return None

        if not portal or not mac:
            self["status"].setText(_("Error: Portal and MAC are required"))
            return None

        # Normalize portal URL
        if not portal.endswith("/c/"):
            portal = portal.rstrip("/") + "/c/"

        # Create session with robust retry settings
        timestamp = int(time.time())
        md5_hash = md5(mac.encode()).hexdigest()
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        host = urlparse(portal).netloc
        timezone = fetch_system_timezone()
        headers = {
            "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Host": host,
            "Referer": portal,
            "Cookie": f"mac={mac}; stb_lang=en; timezone={timezone};",
            "X-User-Agent": "Model: MAG254; Link: Ethernet",
        }

        # Legacy parameters
        idents = {
            "mac": mac,
            "sn": md5_hash,
            "type": "STB",
            "model": "MAG250",
            "uid": "",
            "random": "null"
        }

        legacy_params = {
            'action': 'get_profile',
            'mac': mac,
            'type': 'stb',
            'hd': '1',
            'sn': md5_hash,
            'stb_type': "MAG250",
            'client_type': 'STB',
            'image_version': '218',
            'device_id': "",
            'hw_version': '1.7-BD-00',
            'hw_version_2': '1.7-BD-00',
            'auth_second_step': '1',
            'video_out': 'hdmi',
            'num_banks': '2',
            'ver': "ImageDescription: 0.2.18-r14-pub-250;ImageDate: Fri Jan 15 15:20:44 EET 2016;PORTAL version: 5.6.1;API Version: JS API version: 328;STB API version: 134;Player Engine version: 0x566",
            'not_valid_token': '0',
            'metrics': dumps(idents),
            'timestamp': str(timestamp),
            'api_signature': '262'
        }

        url_templates = {
            "handshake": [
                f"http://{host}/portal.php?type=stb&action=handshake&JsHttpRequest=1-xml",
                f"http://{host}/server/load.php?type=stb&action=handshake&JsHttpRequest=1-xml",
                f"http://{host}/stalker_portal/server/load.php?type=stb&action=handshake&JsHttpRequest=1-xml"
            ],
            "profile": [
                f"http://{host}/portal.php?type=stb&action=get_profile&JsHttpRequest=1-xml",
                f"http://{host}/server/load.php?type=stb&action=get_profile&JsHttpRequest=1-xml",
                f"http://{host}/stalker_portal/server/load.php?type=stb&action=get_profile&JsHttpRequest=1-xml"
            ],
            "account": [
                f"http://{host}/portal.php?type=account_info&action=get_main_info&JsHttpRequest=1-xml",
                f"http://{host}/server/load.php?type=account_info&action=get_main_info&JsHttpRequest=1-xml",
                f"http://{host}/stalker_portal/server/load.php?type=account_info&action=get_main_info&JsHttpRequest=1-xml"
            ],
            "tariff": [
                f"http://{host}/portal.php?type=account_info&action=get_tariff_plan&JsHttpRequest=1-xml",
                f"http://{host}/server/load.php?type=account_info&action=get_tariff_plan&JsHttpRequest=1-xml",
                f"http://{host}/stalker_portal/server/load.php?type=account_info&action=get_tariff_plan&JsHttpRequest=1-xml"
            ]
        }

        info = {
            "user": "",
            "password": "",
            "expiry": "",
            "status": "",
            "active": "",
            "max": ""
        }

        try:
            # Step 1: Handshake
            token = None
            with open("/tmp/stalker_account_info.log", "w") as f_debug:
                f_debug.write("=== Handshake phase ===\n")
                for url in url_templates["handshake"]:
                    try:
                        response = session.get(url, headers=headers, timeout=8)
                        f_debug.write("URL: " + url + "\n")
                        f_debug.write("Response status: " + str(response.status_code) + "\n")
                        if response.status_code == 200:
                            try:
                                data = response.json()
                                f_debug.write("JSON response keys: " + ", ".join(data.keys()) + "\n")
                                token = data.get("js", {}).get("token")
                                if token:
                                    f_debug.write("Token found: " + token + "\n\n")
                                    break
                            except JSONDecodeError:
                                f_debug.write("JSON decode failed, trying manual extraction\n")
                                match = search(r'"token"\s*:\s*"([^"]+)"', response.text)
                                if match:
                                    token = match.group(1)
                                    f_debug.write("Token extracted manually: " + token + "\n\n")
                                    break
                    except (requests.exceptions.RequestException, ConnectionError) as e:
                        f_debug.write("Request exception: " + str(e) + "\n")
                        continue

                if not token:
                    f_debug.write("Error: No token received, aborting\n")

            if not token:
                self.update_status(_("Handshake failed: No token received"))
                return None

            # Step 2: Authentication
            headers_auth = headers.copy()
            headers_auth["Authorization"] = "Bearer " + token
            auth_url = f"http://{host}/portal.php"
            params_auth = {
                "type": "stb",
                "action": "do_auth",
                "mac": mac,
                "token": token,
                "JsHttpRequest": "1-xml"
            }
            session.get(auth_url, params=params_auth, headers=headers_auth, timeout=8)

            # Step 3: Get profile
            profile_data = {}

            with open("/tmp/stalker_account_info.log", "a") as f_debug:
                f_debug.write("\n=== Profile retrieval ===\n")
                for url in url_templates["profile"]:
                    try:
                        response = session.get(url, headers=headers_auth, timeout=8)
                        f_debug.write("URL: " + url + "\n")
                        f_debug.write("Response status: " + str(response.status_code) + "\n")
                        if response.status_code == 200:
                            try:
                                profile_data = response.json().get("js", {})
                                for k, v in profile_data.items():
                                    f_debug.write("{}: {}\n".format(k, v))
                                break
                            except JSONDecodeError:
                                f_debug.write("JSON decode failed for profile\n")
                                continue
                    except (requests.exceptions.RequestException, ConnectionError) as e:
                        f_debug.write("Request exception: " + str(e) + "\n")
                        continue

            if not profile_data:
                with open("/tmp/stalker_account_info.log", "a") as f_debug:
                    f_debug.write("Error: No profile data retrieved\n")

            # Step 4: Get account info (using legacy parameters here)
            account_info = {}

            with open("/tmp/stalker_account_info.log", "a") as f_debug:
                f_debug.write("\n=== Account info retrieval ===\n")
                # Include legacy parameters in the request
                for url in url_templates["account"]:
                    try:
                        response = session.get(url, headers=headers_auth, params=legacy_params, timeout=8)
                        f_debug.write("URL: " + url + "\n")
                        f_debug.write("Response status: " + str(response.status_code) + "\n")
                        if response.status_code == 200:
                            try:
                                account_info = response.json().get("js", {})
                                info["expiry"] = account_info.get("exp_date", "")
                                for k, v in account_info.items():
                                    f_debug.write("{}: {}\n".format(k, v))
                                break
                            except JSONDecodeError:
                                f_debug.write("JSON decode failed for account info\n")
                                continue
                    except (requests.exceptions.RequestException, ConnectionError) as e:
                        f_debug.write("Request exception: " + str(e) + "\n")
                        continue

            if not account_info:
                with open("/tmp/stalker_account_info.log", "a") as f_debug:
                    f_debug.write("Error: No account info retrieved\n")

            tariff_info = {}

            with open("/tmp/stalker_account_info.log", "a") as f_debug:
                f_debug.write("\n=== Tariff info retrieval ===\n")
                for url in url_templates["tariff"]:
                    try:
                        response = session.get(url, headers=headers_auth, timeout=8)
                        f_debug.write("URL: " + url + "\n")
                        f_debug.write("Response status: " + str(response.status_code) + "\n")
                        if response.status_code == 200:
                            try:
                                tariff_info = response.json().get("js", {})
                                info["status"] = tariff_info.get("status", "")
                                info["active"] = tariff_info.get("active", "")
                                info["max"] = tariff_info.get("max_connections", "")
                                for k, v in tariff_info.items():
                                    f_debug.write("{}: {}\n".format(k, v))
                                break
                            except JSONDecodeError:
                                f_debug.write("JSON decode failed for tariff info\n")
                                continue
                    except (requests.exceptions.RequestException, ConnectionError) as e:
                        f_debug.write("Request exception: " + str(e) + "\n")
                        continue

            if not tariff_info:
                with open("/tmp/stalker_account_info.log", "a") as f_debug:
                    f_debug.write("Error: No tariff info retrieved\n")

            info = {
                "user": account_info.get("name", "N/A"),
                "password": profile_data.get("password", "N/A"),
                "expiry": account_info.get("expire_billing_date", "N/A"),
                "status": tariff_info.get("status", "N/A"),
                "active": tariff_info.get("active", "N/A"),
                "max": tariff_info.get("max_connections", "N/A")
            }

            print("[DEBUG] Account Info: {}".format(info))
            return info
        except Exception as e:
            self["status"].setText(_("Error extracting account information"))
            with open("/tmp/stalker_account_info.log", "a") as f_debug:
                f_debug.write("Error: " + str(e) + "\n")
            return None

    def update_account_fields(self, user, password, expiry, status, active, max_conn):
        """Update GUI widgets with account information values"""
        # Format status
        status_map = {"0": "Active", "1": "Disabled", "": "Unknown"}
        status_str = status_map.get(status, status)

        # Format the expiration date
        expiry_str = "Never" if expiry == "0000-00-00 00:00:00" else expiry

        # Update widget
        self["user_value"].setText(user)
        self["pass_value"].setText(password)
        self["expiry_value"].setText(expiry_str)
        self["status_value"].setText(status_str)
        self["active_value"].setText(active)
        self["max_value"].setText(max_conn)

    def clear_account_info(self):
        """Clear account info fields"""
        self["user_value"].setText("")
        self["pass_value"].setText("")
        self["expiry_value"].setText("")
        self["status_value"].setText("")
        self["blocked_value"].setText("")
        self["active_value"].setText("")
        self["max_value"].setText("")

    def get_output_filename(self):
        """Returns the full path of the file"""
        base_dir = config.plugins.stalkerportal.output_dir.value
        mac = config.plugins.stalkerportal.mac_address.value.replace(":", "_")
        if not base_dir.endswith('/'):
            base_dir += '/'
        return f"{base_dir}stalker_{mac}.m3u"

    def validate_and_add_entry(self, portal, mac):
        """Validate and add entry to the list if valid"""
        if portal and mac:
            portal_valid = self.validate_portal_url(portal)
            mac_valid = self.validate_mac_address(mac)

            if portal_valid and mac_valid:
                display = f"{portal} - {mac}"
                return display, portal, mac
            elif not portal_valid:
                self["status"].setText(_(f"Invalid URL: {portal}"))
            elif not mac_valid:
                self["status"].setText(_(f"Invalid MAC: {mac}"))
        return None

    def validate_mac_address(self, mac):
        pattern = compile(r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$')
        return bool(pattern.match(mac))

    def validate_portal_url(self, url):
        """Validate portal if valid"""
        # Clean any double '/c/' path
        url = url.rstrip("/")
        if url.endswith("/c"):
            url += "/"
        elif not url.endswith("/c/"):
            url += "/c/"

        # Optional: prevent multiple /c/c/ if user entered it
        url = url.replace("/c/c/", "/c/")

        pattern = compile(r"^https?://[^\s/$.?#].[^\s]*$")
        return bool(pattern.match(url)), url

    def edit_settings(self):
        """Open settings menu with available Stalker portal options"""
        menu = [
            (_("Set Type Conversion"), self.select_type_convert),
            (_("Edit Portal URL"), self.edit_portal),
            (_("Edit MAC Address"), self.edit_mac),
            (_("Select Playlist File"), self.select_playlist_file),
            (_("Delete Playlist File"), self.delete_playlist),
            (_("Change Output Directory"), self.select_output_dir),
            (_("Upgrade Stalker Converter"), self.check_vers),
            (_("Information"), self.show_info)
        ]
        if config.plugins.stalkerportal.type_convert.value == "1":
            menu.insert(5, (_("Set Bouquet Position"), self.select_bouquet_position))

        self.session.openWithCallback(self.menu_callback, MenuDialog, menu)

    def edit_portal(self):
        """
        Opens a virtual keyboard to edit the portal URL.
        When confirmed, updates the configuration and related UI elements.
        """
        def portal_callback(portal):
            if portal:
                config.plugins.stalkerportal.portal_url.value = portal
                self["portal_input"].setText(portal)
                self["file_input"].setText(self.get_output_filename())
                configfile.save()
                configfile.load()

        self.session.openWithCallback(
            portal_callback,
            VirtualKeyBoard,
            title=_("Enter Portal URL (e.g. http://example.com/c/ or http://example.com:8088/c/)"),
            text=config.plugins.stalkerportal.portal_url.value
        )

    def edit_mac(self):
        """
        Opens a virtual keyboard to edit the MAC address.
        When confirmed, updates the configuration and related UI elements.
        """
        def mac_callback(mac):
            if mac:
                config.plugins.stalkerportal.mac_address.value = mac
                self["mac_input"].setText(mac)
                self["file_input"].setText(self.get_output_filename())
                configfile.save()
                configfile.load()

        self.session.openWithCallback(
            mac_callback,
            VirtualKeyBoard,
            title=_("Enter MAC Address (e.g., 00:1A:79:XX:XX:XX)"),
            text=config.plugins.stalkerportal.mac_address.value
        )

    def menu_callback(self, result):
        if result:
            result[1]()

    def select_type_convert(self):
        """
        Opens a ChoiceBox to select the type of conversion.
        Stores the selected value in the config and updates the green button label.
        """
        options = [
            ("MAC to M3U", "0"),
            ("MAC to .tv", "1")
        ]

        def type_callback(choice):
            if choice:
                config.plugins.stalkerportal.type_convert.value = choice[1]
                configfile.save()
                configfile.load()

                # Update green button label
                if choice[1] == "0":
                    self["key_green"].setText("Convert to M3U")
                else:
                    self["key_green"].setText("Convert to TV")

        self.session.openWithCallback(
            type_callback,
            ChoiceBox,
            title=_("Select Type of Conversion"),
            list=options
        )

    def select_bouquet_position(self):
        """Show dialog to select bouquet position (top or bottom) and save to config"""
        options = [("top", _("Top")), ("bottom", _("Bottom"))]

        def bouquet_pos_callback(choice):
            if choice is not None:
                config.plugins.stalkerportal.bouquet_position.value = choice
                configfile.save()
                configfile.load()
                self.update_status(_("Bouquet position set to: {}").format(choice))

        self.session.openWithCallback(
            bouquet_pos_callback,
            ChoiceBox,
            title=_("Select Bouquet Position"),
            list=[(key, _(val)) for key, val in options]
        )

    def load_playlist(self, file_path):
        """Load playlist from selected file"""
        self.portal_list = []
        valid_count = 0

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = [line.strip() for line in f.readlines()]

            index = 0
            while index < len(lines):
                self["status"].setText(_("Get ALL portal/MAC from file"))
                portals_macs, index = self.parse_playlist_entry(lines, index)

                for portal, mac in portals_macs:
                    entry = self.validate_and_add_entry(portal, mac)
                    if entry:
                        self.portal_list.append(entry)
                        valid_count += 1

            # Update list display
            display_list = [entry[0] for entry in self.portal_list]
            self["file_list"].setList(display_list)

            if valid_count > 0:
                self["status"].setText(_("Loaded {} valid portals from {}").format(valid_count, basename(file_path)))
            else:
                self["status"].setText(_("No valid portals found in file"))

        except Exception as e:
            self["status"].setText(_("Error: ") + str(e))

    def parse_playlist_entry(self, lines, start_index):
        """Parse playlist lines starting at index to extract portal URLs and MACs"""
        mac_regex = compile(r'([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})')
        url_regex = compile(r'https?://[^\s"\'<>]+')
        portals_macs = []
        current_portal = None
        index = start_index
        total_lines = len(lines)

        # Pre-compile patterns for better performance
        portal_pattern = compile(r'portal|panel|real|servidor', IGNORECASE)
        mac_pattern = compile(r'mac', IGNORECASE)

        while index < total_lines:
            line = lines[index].strip()
            if not line:
                index += 1
                continue

            # Update progress
            progress = int((index // total_lines) * 100)
            self["status"].setText(_("Scanning file... {}%").format(progress))

            # Check for portal/panel line
            if portal_pattern.search(line) and not current_portal:
                url_match = url_regex.search(line)
                if url_match:
                    current_portal = url_match.group(0).rstrip('/')
                    # Normalize portal URL
                    if not current_portal.endswith('/c'):
                        current_portal += '/c'
                    index += 1
                    continue

            # Check for MAC address line
            if mac_pattern.search(line):
                mac_match = mac_regex.search(line)
                if mac_match:
                    mac = mac_match.group(0)
                    # Handle multiple formats: "MAC: 00:1A:79..." or "00:1A:79..." alone
                    if current_portal:
                        portals_macs.append((current_portal, mac))
                        current_portal = None
                    else:
                        # Try to use previous portal if available
                        if portals_macs:
                            last_portal, xs = portals_macs[-1]
                            portals_macs.append((last_portal, mac))
                # Continue to next line whether we found MAC or not
                index += 1
                continue

            # If we have a portal but no MAC yet, check if line contains standalone MAC
            if current_portal and not mac_pattern.search(line):
                mac_match = mac_regex.search(line)
                if mac_match:
                    mac = mac_match.group(0)
                    portals_macs.append((current_portal, mac))
                    current_portal = None

            index += 1

        # Handle case where last portal didn't have a MAC
        if current_portal and portals_macs:
            last_portal, last_mac = portals_macs[-1]
            portals_macs.append((current_portal, last_mac))

        return portals_macs, index

    def reset_conversion_state(self):
        """Reset conversion state after stop"""
        self.conversion_running = False
        self.conversion_stopped = False
        self.channels = []
        self["status"].setText(_("Conversion stopped by user"))
        self.stop_timer.stop()

    def finish_conversion(self, success, message):
        """Finalize conversion and schedule UI update"""
        self.conversion_running = False
        self.conversion_stopped = False
        self.conversion_result = (success, message)
        self.result_timer.start(10, True)

    def handle_conversion_result(self):
        """Handle conversion result in main thread"""
        if hasattr(self, 'conversion_result'):
            success, message = self.conversion_result
            # Always reset button
            self["key_green"].setText(self.get_convert_label())

            if success:
                self["status"].setText(_("Success! ") + message)
            else:
                self["status"].setText(_("Error! ") + message)

            # Clean up
            del self.conversion_result
        self.result_timer.stop()

    def convert(self):
        """Convert to M3U file with actual channel list"""
        with open("/tmp/stalker_convert_info.log", "a") as f_debug:
            f_debug.write("=== convert phase ===\n")

            # If conversion is running, stop it and reset state
            if self.conversion_running:
                self.conversion_stopped = True
                self["status"].setText(_("Stopping conversion..."))
                self["key_green"].setText(self.get_convert_label())
                # Start timer for resetting state
                self.stop_timer.start(500, True)
                return

            # START NEW CONVERSION - THIS CODE SHOULD BE OUTSIDE THE 'IF' BLOCK
            output_path = config.plugins.stalkerportal.output_dir.value
            if not has_enough_free_space(output_path, min_bytes_required=100 * 1024 * 1024):
                self["status"].setText(_("Not enough space on {}!").format(output_path))
                return

            try:
                portal = config.plugins.stalkerportal.portal_url.value.strip()
                mac = config.plugins.stalkerportal.mac_address.value.strip()
                output_file = self.get_output_filename()
                f_debug.write("Portal URL: {}\n".format(portal))
                f_debug.write("MAC address: {}\n".format(mac))
                convert_type = config.plugins.stalkerportal.type_convert.value
                if convert_type == "0":
                    f_debug.write("Output file: {}\n".format(output_file))
            except Exception as e:
                self["status"].setText(_("Error accessing configuration values"))
                self.session.open(MessageBox, _("Failed to read portal or MAC configuration:\n") + str(e), MessageBox.TYPE_ERROR)
                return

            # Validate inputs
            if not portal or not mac:
                self["status"].setText(_("Error: Portal and MAC are required"))
                return

            if not self.validate_portal_url(portal):
                self["status"].setText(_("Error: Invalid portal URL"))
                return

            if not self.validate_mac_address(mac):
                self["status"].setText(_("Error: Invalid MAC address"))
                return

            # Ensure output directory exists
            output_dir = dirname(output_file)
            if not exists(output_dir):
                try:
                    makedirs(output_dir)
                except Exception as e:
                    error = _("Cannot create directory: ") + str(e)
                    self["status"].setText(error)
                    return

            # Reset stop flag
            self.conversion_stopped = False
            self.conversion_running = True

            # Update button label
            self["key_green"].setText(_("Stop"))

            # Show initial status
            self["status"].setText(_("Starting conversion process..."))
            f_debug.write("Starting conversion process...\n")

            # Start worker thread
            self.worker_thread = Thread(target=self.convert_thread, args=(portal, mac, output_file))
            self.worker_thread.start()

    def convert_thread(self, portal, mac, output_file):
        """Background thread for conversion process without error popups"""
        try:
            if self.conversion_stopped:
                self.finish_conversion(False, _("Conversion canceled before start"))
                return

            with open("/tmp/stalker_convert_info.log", "a") as f_debug:
                f_debug.write("=== convert_thread phase ===\n")

                # Step 1: Retrieve actual channel list
                self.update_status(_("Step 1/3: Connecting to portal..."))
                success = self.get_channel_list(portal, mac)
                f_debug.write("=== Step 1/3: Connecting to portal... ===\n")

                if self.conversion_stopped:
                    self.finish_conversion(False, _("Conversion stopped during channel retrieval"))
                    return

                if not success or not self.channels:
                    self.finish_conversion(False, _("Failed to retrieve channel list"))
                    return

                # Step 2: Create M3U content with actual channels
                self.update_status(_("Creating playlist file..."))
                convert_type = config.plugins.stalkerportal.type_convert.value
                f_debug.write("Creating playlist file\n")
                f_debug.write("convert_type: " + convert_type + "\n")

                try:
                    if convert_type == "0":
                        with open(output_file, "w", encoding="utf-8") as f:
                            f.write("#EXTM3U\n")
                            f.write("# Portal: {}\n".format(portal))
                            f.write("# MAC: {}\n".format(mac))
                            f.write("# Channels: {}\n\n".format(len(self.channels)))

                            for channel in self.channels:
                                cleaned_name = cleanName(channel["name"])
                                cleaned_group = cleanName(channel["group"])

                                f.write("#EXTINF:-1 tvg-id=\"{}\" tvg-name=\"{}\" ".format(
                                    channel["id"], cleaned_name))
                                f.write("tvg-logo=\"{}\" group-title=\"{}\",{}\n".format(
                                    channel["logo"], cleaned_group, cleaned_name))
                                f.write("{}\n\n".format(channel["url"]))

                        self.update_status(_("M3U created with {} channels").format(len(self.channels)))
                        f_debug.write("M3U created with channels: " + str(len(self.channels)) + "\n")

                    elif convert_type == "1":
                        groups = {}
                        for idx, channel in enumerate(self.channels):
                            if idx % 50 == 0 and self.conversion_stopped:
                                self.finish_conversion(False, _("Conversion stopped during grouping"))
                                return

                            group = channel.get("group", "Default")
                            groups.setdefault(group, []).append(channel)
                            self.update_status(_("Grouping: {}").format(channel["name"]))

                        if self.conversion_stopped:
                            self.finish_conversion(False, _("Conversion stopped before bouquet creation"))
                            return

                        for group, ch_list in groups.items():
                            if self.conversion_stopped:
                                self.finish_conversion(False, _("Conversion stopped during bouquet creation"))
                                return

                            self.write_group_bouquet(group, ch_list)

                        self.update_main_bouquet(groups.keys())
                        self.finish_conversion(True, _("Bouquets created: {} groups with {} channels").format(
                            len(groups), len(self.channels)))
                        f_debug.write("Bouquets created: {} groups with {} channels\n".format(
                            len(groups), len(self.channels)))

                except Exception as e:
                    self.finish_conversion(False, _("Error creating playlist: ") + str(e))
                    f_debug.write("Exception during playlist creation: " + str(e) + "\n")

        except Exception as e:
            self.finish_conversion(False, _("Conversion error: ") + str(e))
            with open("/tmp/stalker_convert_info.log", "a") as f_debug:
                f_debug.write("Global exception: " + str(e) + "\n")

    def write_group_bouquet(self, group, channels):
        """Create .tv bouquet file per group"""
        try:
            safe_name = self.get_safe_filename(group)
            filename = "/etc/enigma2/userbouquet." + safe_name + ".tv"
            temp_file = filename + ".tmp"

            with open(temp_file, "w", encoding="utf-8", errors="replace") as f:
                cleaned_group = cleanName(group) if group else safe_name
                f.write("#NAME {}\n".format(cleaned_group))
                f.write("#SERVICE 1:64:0:0:0:0:0:0:0:0:--- | Stalker2Bouquet | ---\n")
                f.write("#DESCRIPTION --- | Stalker2Bouquet | ---\n")

                for ch in channels:
                    url = ch["url"]
                    if not url.startswith("http"):
                        url = "http://" + url

                    encoded_url = url.replace(":", "%3a")
                    f.write("#SERVICE 4097:0:1:0:0:0:0:0:0:0:")
                    f.write(encoded_url)
                    f.write("\n")

                    f.write("#DESCRIPTION ")
                    f.write(ch["name"])
                    f.write("\n")
            replace(temp_file, filename)
            chmod(filename, 0o644)

        except Exception as e:
            print(f"[ERROR] write_group_bouquet exception: {e}")
            if exists(temp_file):
                try:
                    remove(temp_file)
                except:
                    pass
            raise

    def update_main_bouquet(self, groups):
        """Update the main bouquet file with generated group bouquets"""
        main_file = "/etc/enigma2/bouquets.tv"
        existing = []

        if exists(main_file):
            with open(main_file, "r", encoding="utf-8") as f:
                existing = f.readlines()
        new_lines = []
        for group in groups:
            safe_name = self.get_safe_filename(group)
            bouquet_path = "userbouquet." + safe_name + ".tv"
            line_to_add = '#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "' + bouquet_path + '" ORDER BY bouquet\n'

            if line_to_add not in existing and line_to_add not in new_lines:
                new_lines.append(line_to_add)

        # Decide if add at top or bottom based on config option
        if config.plugins.stalkerportal.bouquet_position.value == "top":
            new_content = new_lines + existing
        else:
            new_content = existing + new_lines

        with open(main_file, "w", encoding="utf-8") as f:
            f.writelines(new_content)

        from twisted.internet import reactor
        reactor.callFromThread(reload_services)

    def get_safe_filename(self, group):
        """Sanitize filename for Enigma2 bouquet"""
        # Handle empty group names
        if not group or not group.strip():
            # Use MAC address as fallback
            mac = config.plugins.stalkerportal.mac_address.value.replace(":", "_")
            return f"stalker_{mac}"

        # Basic sanitization
        safe_name = unicodedata.normalize("NFKD", group)
        safe_name = safe_name.encode("ascii", "ignore").decode("ascii")

        # Replace spaces and special characters
        safe_name = sub(r"[^a-z0-9_\- ]", "_", safe_name.lower())
        safe_name = sub(r"\s+", "_", safe_name)  # Replace spaces with underscores
        safe_name = sub(r"_+", "_", safe_name).strip("_")

        # If we have nothing left after sanitization
        if not safe_name:
            # Final fallback to MAC
            mac = config.plugins.stalkerportal.mac_address.value.replace(":", "_")
            return f"stalker_{mac}"

        # Truncate but preserve the end
        max_len = 40
        if len(safe_name) > max_len:
            safe_name = safe_name[-max_len:]

        return safe_name + "_stalker"

    def _finish_conversion_safe(self, success, message):
        """Update UI in main thread"""
        # Always reset button first
        self["key_green"].setText(self.get_convert_label())

        if success:
            self["status"].setText(_("Success! ") + message)
        else:
            self["status"].setText(_("Error! ") + message)

        # Clear processing state
        self.conversion_running = False
        self.conversion_stopped = False

    def update_account_info(self, text):
        """Thread-safe account info update with debug"""
        print(f"[DEBUG] Updating account info with text: {text}")
        from twisted.internet import reactor
        reactor.callFromThread(self._update_account_info_safe, text)

    def _update_account_info_safe(self, text):
        """Update account info in main thread with debug"""
        print(f"[DEBUG] Setting account_info widget to: {text}")
        self["account_info"].setText(text)
        self["account_info"].instance.invalidate()

    def update_status(self, text):
        """Thread-safe status update"""
        from twisted.internet import reactor
        reactor.callFromThread(self._update_status_safe, text)

    def _update_status_safe(self, text):
        """Update status in main thread"""
        self["status"].setText(text)

    def get_channel_list(self, portal, mac):
        """Retrieve channel list with robust connection handling"""
        self.channels = []
        try:
            # Create main session with connection pooling
            session = requests.Session()

            # Configure advanced retry strategy
            retry_strategy = Retry(
                total=5,  # Increased total retries
                backoff_factor=1.5,  # More aggressive backoff
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET"],
                raise_on_status=False
            )

            # Configure adapter with larger connection pool
            adapter = HTTPAdapter(
                max_retries=retry_strategy,
                pool_connections=50,
                pool_maxsize=100,
                pool_block=True
            )
            session.mount("http://", adapter)
            session.mount("https://", adapter)

            portal = portal.rstrip("/")
            link_api = portal + "/portal.php"
            host = urlparse(portal).netloc
            timezone = fetch_system_timezone()
            headers = {
                "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/537.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/537.3",
                "Accept": "*/*",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "Host": host,
                "Referer": portal,
                "Cookie": f"mac={mac}; stb_lang=en; timezone={timezone};",
                "X-User-Agent": "Model: MAG254; Link: Ethernet"
            }

            # Step 1: Handshake
            self.update_status(_("Step 1/3: Handshake..."))
            params_handshake = {"type": "stb", "action": "handshake", "token": "", "JsHttpRequest": "1-xml"}
            response = session.get(link_api, params=params_handshake, headers=headers, timeout=10)
            token_match = search(r'"token"\s*:\s*"([^"]+)"', response.text)
            if not token_match:
                self.update_status(_("Failed to obtain token!"))
                return False

            token = token_match.group(1)

            # Step 2: Authentication
            self.update_status(_("Step 2/3: Authentication..."))
            headers["Authorization"] = f"Bearer {token}"
            params_auth = {"type": "stb", "action": "do_auth", "mac": mac, "token": token, "JsHttpRequest": "1-xml"}
            session.get(link_api, params=params_auth, headers=headers, timeout=10)

            # Step 3: Get channel list
            self.update_status(_("Step 3/3: Getting channels..."))
            params_channels = {"type": "itv", "action": "get_all_channels", "JsHttpRequest": "1-xml"}
            response = session.get(link_api, params=params_channels, headers=headers, timeout=20)
            json_data = response.json()
            channels_data = json_data.get("js", {}).get("data", [])
            total_channels = len(channels_data)

            if not channels_data:
                self.update_status(_("No channels found in response"))
                return False

            # Prepare for parallel processing
            self.update_status(_("Preparing {} channels...").format(total_channels))
            start_time = time.time()
            last_update = time.time()
            processed = 0

            # Use ThreadPoolExecutor for parallel processing
            max_workers = min(10, total_channels)  # Max 10 workers
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all channels for processing
                future_to_channel = {}
                for channel in channels_data:
                    # Check if stopped
                    if self.conversion_stopped:
                        self.update_status(_("Conversion stopped during channel processing"))
                        return False

                    future = executor.submit(
                        self.process_channel,
                        session,
                        portal,
                        headers,
                        channel,
                        mac,
                        token
                    )
                    future_to_channel[future] = channel

                # Process results as they complete
                for future in as_completed(future_to_channel):
                    # Check if stopped
                    if self.conversion_stopped:
                        self.update_status(_("Conversion stopped during result processing"))
                        return False

                    try:
                        channel_data = future.result()
                        if channel_data:
                            self.channels.append(channel_data)
                            processed += 1
                    except Exception as e:
                        print(f"Error processing channel: {str(e)}")

                    # Update progress every 10 channels or every 5 seconds
                    current_time = time.time()
                    if processed % 100 == 0 or current_time - last_update > 5:
                        elapsed = current_time - start_time
                        speed = processed / elapsed if elapsed > 0 else 0
                        self.update_status(
                            _("Processed {}/{} channels ({:.1f}/s)").format(processed, total_channels, speed)
                        )
                        last_update = current_time

            if self.conversion_stopped:
                return False

            elapsed = time.time() - start_time
            speed = len(self.channels) / elapsed if elapsed > 0 else 0
            self.update_status(
                _("Processed {} of {} channels in {:.1f}s ({:.1f}/s)").format(
                    len(self.channels), total_channels, elapsed, speed
                )
            )
            return True

        except Exception as e:
            print('error stalker : ', e)
            # self.update_status(_("Channel processing error: ") + str(e))
            return False

    def process_channel(self, session, portal, headers, channel, mac, token):
        """Process individual channel with enhanced reliability and Xtream compatibility"""
        try:
            cmd = channel.get("cmd", "").strip()
            channel_id = channel.get("id", "")
            channel_name = channel.get("name", "Unknown")

            # Build API URL
            channel_params = {
                "type": "itv",
                "action": "create_link",
                "cmd": cmd,
                "mac": mac,
                "token": token,
                "JsHttpRequest": "1-xml"
            }
            api_url = f"{portal.rstrip('/')}/portal.php?{urlencode(channel_params)}"

            # Extract domain for later use
            parsed_portal = urlparse(portal)
            domain = parsed_portal.netloc

            # Enhanced retry strategy
            max_retries = 3
            for attempt in range(max_retries + 1):
                try:
                    # Create a new session for this channel
                    with requests.Session() as channel_session:
                        # Configure retry strategy
                        retry_strategy = Retry(
                            total=3,
                            backoff_factor=0.7,
                            status_forcelist=[429, 500, 502, 503, 504],
                            allowed_methods=["GET"]
                        )
                        adapter = HTTPAdapter(max_retries=retry_strategy)
                        channel_session.mount('http://', adapter)
                        channel_session.mount('https://', adapter)
                        channel_session.verify = False  # Bypass SSL verification

                    # # Custom SSL context
                    ssl_context = ssl.create_default_context()
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE

                    # Rotate User-Agent
                    user_agents = [
                        "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/537.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/537.3",
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                    ]
                    current_headers = headers.copy()
                    current_headers["User-Agent"] = random.choice(user_agents)

                    # Make the request
                    response = channel_session.get(
                        api_url,
                        headers=current_headers,
                        timeout=(3.05, 10),
                        allow_redirects=True
                    )

                    # Handle server errors
                    if response.status_code >= 500:
                        if attempt < max_retries:
                            wait_time = 0.5 * (2 ** attempt)  # Exponential backoff
                            time.sleep(wait_time)
                            continue
                        else:
                            response.raise_for_status()

                    # Handle empty responses
                    if not response.content:
                        raise ValueError("Empty response from server")

                    # Parse JSON - try both standard and manual extraction
                    try:
                        json_data = response.json()
                    except JSONDecodeError:
                        # Attempt manual extraction for malformed responses
                        json_match = search(r'\{.*\}', response.text)
                        if json_match:
                            try:
                                json_data = loads(json_match.group())
                            except:
                                json_data = {"js": {"cmd": api_url}}  # Fallback to API URL
                        else:
                            # Try Xtream-style response
                            if response.text.startswith("http"):
                                return {
                                    "id": str(channel_id),
                                    "name": cleanName(channel_name),
                                    "number": int(channel.get("number", 0)),
                                    "group": channel.get("category_name") or channel.get("group_name") or "",
                                    "logo": str(channel.get("logo", "")),
                                    "url": response.text.strip()
                                }
                            else:
                                raise ValueError("Invalid JSON response")

                    # Extract stream URL using multiple possible patterns
                    stream_url = None

                    # Pattern 1: Standard response
                    if json_data.get("js", {}).get("cmd"):
                        stream_url = json_data["js"]["cmd"]

                    # Pattern 2: Xtream-style response
                    elif "url" in json_data:
                        stream_url = json_data["url"]

                    # Pattern 3: Direct stream in text
                    elif isinstance(json_data, str) and json_data.startswith("http"):
                        stream_url = json_data

                    # Fallback to API URL if no stream found
                    if not stream_url:
                        stream_url = api_url

                    # Clean up the stream URL
                    if stream_url.startswith('ffmpeg '):
                        stream_url = stream_url[7:]

                    # Replace localhost with actual domain
                    if "localhost" in stream_url:
                        stream_url = stream_url.replace("localhost", domain)

                    # Extract credentials from URL (Xtream-style)
                    username, password = "", ""
                    if "xtream" in stream_url or "player_api" in stream_url:
                        cred_match = search(r'http://[^/]+/movie/([^/]+)/([^/]+)/', stream_url)
                        if cred_match:
                            username = cred_match.group(1)
                            password = cred_match.group(2)

                    # Extract channel information
                    group_title = channel.get("group_name", "") or channel.get("category_name", "")

                    # Process name for display
                    if "," in channel_name:
                        display_name = channel_name.split(",", 1)[1].strip()
                    else:
                        display_name = channel_name

                    return {
                        "id": str(channel_id),
                        "name": cleanName(display_name),
                        "number": int(channel.get("number", 0)),
                        "group": group_title,
                        "logo": str(channel.get("logo", "")),
                        "url": stream_url,
                        "username": username,  # For account info
                        "password": password   # For account info
                    }

                except SSLError as ssl_error:
                    print('process_channel except ssl error: ', ssl_error)
                    # Fallback without SSL verification
                    try:
                        response = requests.get(
                            api_url,
                            headers=headers,
                            timeout=(3.05, 10),
                            verify=False
                        )
                        if response.status_code == 200:
                            # Use same processing as above
                            return self.process_fallback_response(response, channel_id, channel_name, channel, api_url, domain)
                    except Exception as fallback_error:
                        print('process_channel error fallback_error: ', fallback_error)
                        pass

                    if attempt < max_retries:
                        time.sleep(1.0 * (2 ** attempt))
                        continue
                    else:
                        return None

                except RequestException as e:
                    print('process_channel error attempt 1: ', e)
                    if attempt < max_retries:
                        time.sleep(0.8 * (2 ** attempt))
                        continue
                    else:
                        return None

                except Exception as e:
                    print('process_channel error attempt 2: ', e)
                    if attempt < max_retries:
                        time.sleep(0.5 * (2 ** attempt))
                        continue
                    else:
                        return None

            return None

        except Exception as e:
            print('process_channel Exception error: ', e)
            return None

    def process_fallback_response(self, response, channel_id, channel_name, channel, api_url, domain):
        """Process response from fallback SSL request"""
        try:
            # Try standard JSON parsing
            try:
                json_data = response.json()
                stream_url = json_data.get("js", {}).get("cmd", api_url)
            except:
                # Try to extract URL directly
                if response.text.startswith("http"):
                    stream_url = response.text.strip()
                else:
                    stream_url = api_url

            # Clean URL
            if stream_url.startswith('ffmpeg '):
                stream_url = stream_url[7:]
            if "localhost" in stream_url:
                stream_url = stream_url.replace("localhost", domain)

            return {
                "id": str(channel_id),
                "name": cleanName(channel_name),
                "number": int(channel.get("number", 0)),
                "group": channel.get("category_name") or channel.get("group_name") or "",
                "logo": str(channel.get("logo", "")),
                "url": stream_url
            }
        except:
            return None

    def select_output_dir(self):
        """Select output directory only - without file browsing"""
        devices = get_mounted_devices()
        if not devices:
            self["status"].setText(_("No writable devices found!"))
            return

        choices = []
        for path, desc in devices:
            try:
                # Calcola spazio libero
                stat = statvfs(path)
                free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
                choices.append((f"{desc} ({free_gb:.1f} GB free)", path))
            except:
                choices.append((desc, path))

        choices.append((_("<< Cancel"), None))
        self.session.openWithCallback(
            self.device_selected,
            ChoiceBox,
            title=_("Select storage device"),
            list=choices
        )

    def device_selected(self, choice):
        """Device selection management - only set directory"""
        if choice and choice[1]:
            config.plugins.stalkerportal.output_dir.value = choice[1]
            self["file_input"].setText(self.get_output_filename())
            self["status"].setText(_("Selected device: ") + choice[0])
            configfile.save()
            configfile.load()

    def select_playlist_file(self):
        """Select a playlist file - new function for yellow button"""
        self.browse_directory(config.plugins.stalkerportal.output_dir.value)

    def browse_directory(self, path=None):
        """Browse files and directories to select a playlist file"""
        if path is None:
            path = config.plugins.stalkerportal.output_dir.value

        if not exists(path):
            try:
                makedirs(path, exist_ok=True)
            except:
                path = "/tmp"

        files = []
        parent_dir = dirname(path)
        if parent_dir and parent_dir != path and exists(parent_dir):
            files.append(("[DIR] ..", parent_dir))

        try:
            for item in sorted(listdir(path)):
                full_path = join(path, item)
                if isdir(full_path):
                    files.append((f"[DIR] {item}", full_path))
                elif item.lower().endswith(('.txt', '.list', '.m3u')):
                    try:
                        size = getsize(full_path)
                        human_size = self.format_size(size)
                        files.append((f"{item} ({human_size})", full_path))
                    except:
                        files.append((item, full_path))

        except Exception as e:
            self["status"].setText(_("Error accessing directory: ") + str(e))
            return

        if files:
            files.insert(0, (_("<< Back to main menu"), None))
            self.session.openWithCallback(
                self.file_selected,
                ChoiceBox,
                title=_("Select playlist file in: {}").format(path),
                list=files
            )
        else:
            self["status"].setText(_("No files found in: ") + path)

    def file_selected(self, choice):
        """Handle directory or file selection"""
        if not choice:
            return

        if choice[1] is None:
            return

        if choice[0].startswith("[DIR]"):
            self.browse_directory(choice[1])

        elif exists(choice[1]):
            self.playlist_file = choice[1]
            self.load_playlist(choice[1])
            config.plugins.stalkerportal.output_dir.value = dirname(choice[1])
            self["file_input"].setText(self.get_output_filename())

            self.select_portal()

            self["status"].setText(_("Loaded playlist: %s. Are you ready to convert? Press Green to proceed.") % basename(choice[1]))

            self.get_convert_label()

            configfile.save()
            configfile.load()

        else:
            self["status"].setText(_("File not found: ") + choice[1])

    def delete_playlist(self, path=None):
        """Browse files and directories to select for deletion"""
        if path is None:
            path = config.plugins.stalkerportal.output_dir.value
            if not exists(path):
                path = defaultMoviePath()

        files = []
        path = path.rstrip('/') or '/'
        parent_dir = dirname(path)
        if parent_dir and parent_dir != path:  # Prevent root loop
            files.append(("[DIR] ..", parent_dir))

        try:
            dir_contents = sorted(listdir(path))
            dirs = [f for f in dir_contents if isdir(join(path, f))]
            file_list = [f for f in dir_contents if isfile(join(path, f)) and f.lower().endswith(('.m3u', '.txt', '.list'))]

            for d in sorted(dirs):
                full_path = join(path, d)
                files.append(("[DIR] " + d, full_path))

            for f in sorted(file_list):
                full_path = join(path, f)
                try:
                    size = self.format_size(getsize(full_path))
                    files.append((f"{f} ({size})", full_path))
                except Exception as e:
                    print(f"Error processing file {f}: {e}")
                    files.append((f, full_path))

        except Exception as e:
            self["status"].setText(_("Error accessing directory: ") + str(e))
            return

        if not files:
            self["status"].setText(_("No files or directories found in: ") + path)
            return

        files.insert(0, (_("<< Back to main menu"), None))

        self.session.openWithCallback(
            self.playlist_selected_for_deletion,
            ChoiceBox,
            title=_("Select file to delete in: {}").format(path),
            list=files
        )

    def playlist_selected_for_deletion(self, choice):
        """Handle file selection for deletion"""
        if not choice:
            return

        if choice[1] is None:
            return

        if choice[0].startswith("[DIR]"):
            self.delete_playlist(choice[1])
            return

        file_path = choice[1]
        filename = basename(file_path)

        message = _("Are you sure you want to delete:\n{}?").format(filename)
        self.session.openWithCallback(
            lambda result: self.confirm_delete(result, file_path),
            MessageBox,
            message,
            MessageBox.TYPE_YESNO
        )

    def confirm_delete(self, result, file_path):
        """Actually delete the file if confirmed"""
        if result:
            try:
                current_dir = dirname(file_path)

                remove(file_path)
                self["status"].setText(_("Deleted: ") + basename(file_path))

                if hasattr(self, 'playlist_file') and self.playlist_file == file_path:
                    self.playlist_file = None
                    self["file_input"].setText("")

                self.delete_playlist(current_dir)

            except Exception as e:
                self["status"].setText(_("Delete failed: ") + str(e))
                self.delete_playlist(dirname(file_path))

    def format_size(self, size_bytes):
        """Convert file size to human-readable format"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} GB"

    def update_path(self):
        """Update path with special handling for /tmp"""
        try:
            base_path = config.plugins.stalkerportal.output_dir.value

            if not base_path or not isdir(base_path):
                fallbacks = ["/media/hdd", "/media/usb", "/tmp"]
                base_path = next((p for p in fallbacks if isdir(p)), "/tmp")

            if base_path == "/tmp":
                self.full_path = base_path
            else:
                self.full_path = join(base_path, "movie")
                if not isdir(self.full_path):
                    makedirs(self.full_path, exist_ok=True)
                    chmod(self.full_path, 0o755)

            if not isdir(self.full_path):
                self.full_path = "/tmp"

        except Exception as e:
            self.full_path = "/tmp"
            print(f"update_path error: {str(e)}")

    def get_convert_label(self):
        """Returns the correct label for the GREEN key"""
        convert_type = config.plugins.stalkerportal.type_convert.value
        if convert_type == "0":
            return _("Convert to M3U")
        elif convert_type == "1":
            return _("Convert to TV")
        # Default label if type is not set
        return _("")

    def keyUp(self):
        self["file_list"].up()

    def keyDown(self):
        self["file_list"].down()

    def keyLeft(self):
        self["file_list"].pageUp()

    def keyRight(self):
        self["file_list"].pageDown()

    def show_info(self):
        text = _(
            "Plugin: StalkerPortalConvert to M3U/TV\n"
            "Author: Lululla\n"
            "Version: %s\n"
            "Date: June 2025\n"
            "Description: Convert Stalker Portal playlists to M3U or Enigma2 bouquet format.\n"
            "Support: linuxsat-support.com - corvoboys.org\n\n"
        ) % currversion

        text += _("HELP:\n")
        text += _("- Open a file with server URLs or press the BLUE button to edit the host and MAC address\n")
        text += _("- Choose the desired output format: M3U or TV bouquet\n")
        text += _("- Press CONVERT to generate the playlist")

        self.session.open(MessageBox, text, MessageBox.TYPE_INFO, timeout=10)

    def clear_fields(self):
        """Clear input fields"""
        config.plugins.stalkerportal.portal_url.value = "http://my.server.xyz:8080/c/"
        config.plugins.stalkerportal.mac_address.value = "00:1A:79:00:00:00"
        self["portal_input"].setText(config.plugins.stalkerportal.portal_url.value)
        self["mac_input"].setText(config.plugins.stalkerportal.mac_address.value)
        self["file_input"].setText(self.get_output_filename())
        self["status"].setText(_("Fields cleared"))
        configfile.save()
        configfile.load()

    def check_vers(self):
        remote_version = "0.0"
        remote_changelog = ""

        try:
            req = Request(b64decoder(installer_url), headers={"User-Agent": AgentRequest})
            page = urlopen(req).read().decode("utf-8")
        except Exception as e:
            print("[ERROR] Unable to fetch version info:", str(e))
            self.defer_message(_("Unable to fetch version info:\n{}").format(str(e)), MessageBox.TYPE_ERROR)
            return

        for line in page.split("\n"):
            line = line.strip()
            if line.startswith("version"):
                remote_version = line.split("=")[-1].strip().strip("'").strip('"')
            elif line.startswith("changelog"):
                remote_changelog = line.split("=")[-1].strip().strip("'").strip('"')
                break

        self.new_version = str(remote_version)
        self.new_changelog = str(remote_changelog)

        if currversion < self.new_version:
            self.Update = True
            self.select_update()
        else:
            self.defer_message(_("You are already running the latest version: {}").format(currversion), MessageBox.TYPE_INFO)

    def select_update(self):
        self.Update = False
        self.new_version, self.new_changelog, update_available = check_version(
            currversion, installer_url, AgentRequest
        )

        if update_available:
            self.Update = True
            print("A new version is available:", self.new_version)

            def ask_update():
                self.session.openWithCallback(
                    self.install_update,
                    MessageBox,
                    _("New version %s available\n\nChangelog: %s\n\nDo you want to install it now?") % (
                        self.new_version, self.new_changelog
                    ),
                    MessageBox.TYPE_YESNO
                )

            self._defer_timer = eTimer()
            self._defer_timer.callback.append(ask_update)
            self._defer_timer.start(100, True)  # 100ms delay

        else:
            print("No new version available.")
            self.defer_message(
                _("You are already running the latest version: %s") % currversion,
                MessageBox.TYPE_INFO
            )

    def install_update(self, answer=False):
        if answer:
            self.session.open(Console, "Upgrading...", cmdlist=["wget -q --no-check-certificate " + b64decoder(installer_url) + " -O - | /bin/sh"], finishedCallback=self.myCallback, closeOnSuccess=False)
        else:
            self.session.open(MessageBox, _("Update Aborted!"), MessageBox.TYPE_INFO, timeout=3)

    def myCallback(self, result=None):
        print("result:", result)
        return

    def defer_message(self, text, mtype=MessageBox.TYPE_INFO):
        """Show message with a short delay to avoid UI modal conflicts"""
        self._defer_timer = eTimer()
        self._defer_timer.callback.append(lambda: self.session.open(MessageBox, text, type=mtype))
        self._defer_timer.start(100, True)


class MenuDialog(Screen):
    skin = """
    <screen name="MenuDialog" position="center,center" size="600,400" title="Edit Settings">
        <widget name="menu" position="10,10" size="580,380" itemHeight="40" font="Regular;28" scrollbarMode="showOnDemand" />
    </screen>
    """

    def __init__(self, session, menu):
        Screen.__init__(self, session)
        self["menu"] = MenuList(menu)
        self["actions"] = ActionMap(
            ["OkCancelActions"],
            {
                "ok": self.ok,
                "cancel": self.cancel,
            }, -1
        )

    def ok(self):
        selection = self["menu"].getCurrent()
        if selection:
            self.close(selection)

    def cancel(self):
        self.close(None)


def main(session):
    session.open(StalkerPortalConverter)


def Plugins(**kwargs):
    return PluginDescriptor(
        name="Stalker Portal Converter",
        description=_("Convert Stalker Portal to M3U playlist with actual channels"),
        where=PluginDescriptor.WHERE_PLUGINMENU,
        icon="plugin.png",
        fnc=main
    )
