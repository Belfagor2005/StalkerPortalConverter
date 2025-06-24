# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

"""
#########################################################
#                                                       #
#  Stalker Portal Converter Plugin                      #
#  Version: 1.4                                         #
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
	replace,
	urandom,
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
from twisted.web import server, resource
from twisted.internet import reactor
import ssl
import socket

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from Components.config import config, configfile, ConfigSelection, ConfigSubsection, ConfigText, ConfigYesNo

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


currversion = '1.5'

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
config.plugins.stalkerportal.input_filelist = ConfigText(default="/etc/enigma2")
config.plugins.stalkerportal.type_convert = ConfigSelection(
	default="0",
	choices=[
		("0", "MAC to M3U"),
		("1", "MAC to .tv")
	]
)
config.plugins.stalkerportal.include_vod = ConfigYesNo(default=True)

config.plugins.stalkerportal.bouquet_position = ConfigSelection(
	default="bottom",
	choices=[("top", _("Top")), ("bottom", _("Bottom"))]
)
config.plugins.stalkerportal.portal_url = ConfigText(default="http://my.server.xyz:8080/c/", fixed_size=False)
config.plugins.stalkerportal.mac_address = ConfigText(default="00:1A:79:00:00:00", fixed_size=False)
config.plugins.stalkerportal.web_access_code = ConfigText(default="", fixed_size=False)

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


# issue on this version: Max retries exceeded with url: fixed


class StalkerPortalConverter(Screen):
	skin = """
		<screen name="StalkerPortalConverter" position="320,179" size="1280,720" title="Stalker Portal Converter" backgroundColor="#16000000">
			<widget name="title" position="10,5" size="1260,40" font="Regular;30" halign="center" foregroundColor="#00ffff" />

			<!-- Label -->
			<widget name="portal_label" position="10,50" size="300,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
			<widget name="portal_input" position="315,50" size="950,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
			<widget name="mac_label" position="10,80" size="300,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
			<widget name="mac_input" position="315,80" size="950,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
			<widget name="file_label" position="10,140" size="300,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
			<widget name="file_input" position="315,140" size="950,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
			<widget name="file_path_label" position="10,110" size="300,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
			<widget name="file_path_input" position="315,110" size="950,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />

			<!-- Label Extended -->
			<widget name="user_label" position="10,505" size="200,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
			<widget name="user_value" position="215,505" size="300,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
			<widget name="pass_label" position="535,505" size="200,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
			<widget name="pass_value" position="740,505" size="240,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
			<widget name="expiry_label" position="10,535" size="200,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
			<widget name="expiry_value" position="215,535" size="300,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
			<widget name="status_label" position="535,535" size="200,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
			<widget name="status_value" position="740,535" size="240,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
			<widget name="active_label" position="10,565" size="200,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
			<widget name="active_value" position="215,565" size="300,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
			<widget name="max_label" position="535,565" size="200,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
			<widget name="max_value" position="740,565" size="240,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
			<widget name="status" position="9,602" size="985,59" font="Regular;24" foregroundColor="#00ff00" halign="center" zPosition="2" />
			<widget name="account_info" position="6,173" size="1260,45" font="Regular;22" foregroundColor="#00ff00" halign="center" zPosition="2" cornerRadius="10" />
			<widget name="portal_list_label" position="8,221" size="1260,30" font="Regular;24" foregroundColor="#ffff00" zPosition="2" scrollbarMode="showNever" />

			<!-- List -->
			<widget name="file_list" position="10,253" size="1260,239" scrollbarMode="showOnDemand" itemHeight="40" font="Regular;28" backgroundColor="#252525" />

			<!-- Buttons -->
			<ePixmap position="10,670" pixmap="skin_default/buttons/red.png" size="30,30" alphatest="blend" zPosition="2" />
			<widget name="key_red" font="Regular;28" position="40,670" size="200,30" halign="left" backgroundColor="black" zPosition="1" transparent="1" cornerRadius="10" />
			<ePixmap position="245,670" pixmap="skin_default/buttons/green.png" size="30,30" alphatest="blend" zPosition="2" />
			<widget name="key_green" font="Regular;28" position="280,670" size="200,30" halign="left" backgroundColor="black" zPosition="2" transparent="1" cornerRadius="10" />
			<ePixmap position="490,670" pixmap="skin_default/buttons/yellow.png" size="30,30" alphatest="blend" zPosition="2" />
			<widget name="key_yellow" font="Regular;28" position="525,670" size="200,30" halign="left" backgroundColor="black" zPosition="2" transparent="1" cornerRadius="10" />
			<ePixmap position="730,670" pixmap="skin_default/buttons/blue.png" size="30,30" alphatest="blend" zPosition="2" />
			<widget name="key_blue" font="Regular;28" position="765,670" size="200,30" halign="left" backgroundColor="black" zPosition="2" transparent="1" cornerRadius="10" />
			<eLabel name="" position="995,664" size="52,40" backgroundColor="#00ffff" foregroundColor="#000000" halign="center" valign="center" transparent="0" cornerRadius="10" font="Regular; 17" zPosition="1" text="OK" />
			<eLabel name="" position="1065,664" size="52,40" backgroundColor="#00ffff" foregroundColor="#000000" halign="center" valign="center" transparent="0" cornerRadius="10" font="Regular; 17" zPosition="1" text="INFO" />
			<eLabel name="" position="1135,664" size="52,40" backgroundColor="#00ffff" foregroundColor="#000000" halign="center" valign="center" transparent="0" cornerRadius="10" font="Regular; 17" zPosition="1" text="EXIT" />

			<!-- server web -->
			<widget name="key_web" position="995,620" size="265,44" font="Regular;22" halign="center" backgroundColor="#b3b3b3" foregroundColor="#000000" cornerRadius="10" />
			<eLabel name="" position="1200,664" size="60,40" backgroundColor="#ffff00" foregroundColor="#000000" halign="center" valign="center" transparent="0" cornerRadius="10" font="Regular; 17" zPosition="1" text="TXT WEB" />
			<widget name="access_code_label" position="995,580" size="265,40" font="Regular;22" halign="center" backgroundColor="#ff8080" foregroundColor="#000000" zPosition="2" cornerRadius="10" />
			<widget name="regen_code_btn" position="1047,540" size="213,40" font="Regular;24" backgroundColor="#00ffff" foregroundColor="#000000" cornerRadius="10" />
			<widget name="show_code_btn" position="1047,500" size="213,40" font="Regular;24" backgroundColor="#00ffff" foregroundColor="#000000" cornerRadius="10" />
			<eLabel name="" position="995,540" size="52,40" backgroundColor="#00ffff" foregroundColor="#000000" halign="center" valign="center" transparent="0" cornerRadius="10" font="Regular; 24" zPosition="1" text="0" />
			<eLabel name="" position="995,500" size="52,40" backgroundColor="#00ffff" foregroundColor="#000000" halign="center" valign="center" transparent="0" cornerRadius="10" font="Regular; 24" zPosition="1" text="1" />
		</screen>
		"""

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

		# Generate a random passcode if none exists
		if not config.plugins.stalkerportal.web_access_code.value:
			self.generate_access_code()

		# Timer to hide the code
		self.access_code_timer = eTimer()
		self.access_code_timer.callback.append(self.hide_access_code)
		self.access_code_visible = False

		self.update_timer = eTimer()
		self.update_timer.callback.append(self.check_for_updates)
		self.update_timer.start(2000)

		self.update_flag_file = "/tmp/stalker_update_flag"

		self["title"] = Label(_("Stalker Portal to M3U Converter v.%s") % currversion)
		self["portal_label"] = Label(_("Portal URL:"))
		self["portal_input"] = Label(config.plugins.stalkerportal.portal_url.value)
		self["mac_label"] = Label(_("MAC Address:"))
		self["mac_input"] = Label(config.plugins.stalkerportal.mac_address.value)
		self["file_label"] = Label(_("Output File:"))
		self["file_input"] = Label("")
		self["file_path_label"] = Label(_("Playlist File:"))
		self["file_path_input"] = Label("")
		self["portal_list_label"] = Label(_("Valid Portals from Selected File:"))

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

		self["key_web"] = Label(_("Web Portal Server Off"))
		self["hint_label"] = Label(_("Press 0: Show Access Code | Press 1: New Code"))
		self["access_code_label"] = Label(_("Access Code: ") + self.get_masked_access_code())
		self["regen_code_btn"] = Label(_("New Code"))
		self["show_code_btn"] = Label(_("Show Code"))

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
				"up": self.keyUp,
				"down": self.keyDown,
				"left": self.keyLeft,
				"right": self.keyRight,
				"ok": self.select_portal,
				"web": self.start_web_server,
				"regenCode": self.regenerate_access_code,  # 0
				"showCode": self.toggle_access_code_visibility,  # 1
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

		self.web_server = None
		self.listening_port = None

		# timer stopping conversion
		# Create timers
		self.stop_timer = eTimer()
		self.stop_timer.timeout.get().append(self.reset_conversion_state)
		self.result_timer = eTimer()
		self.result_timer.timeout.get().append(self.handle_conversion_result)
		self.onLayoutFinish.append(self.initialize_ui)

	def initialize_ui(self):
		"""Deferred user interface initialization"""
		if "file_input" not in self:
			self["file_input"] = Label("")
		vod_status = _("Enabled") if config.plugins.stalkerportal.include_vod.value else _("Disabled")
		self["status"].setText(_("VOD inclusion: {}").format(vod_status))
		self.update_all_widgets()
		self.extract_account_info_async()

	def update_all_widgets(self):
		"""Update all widgets with current values"""
		try:
			# print(f"[DEBUG] Updating widgets - portal: {config.plugins.stalkerportal.portal_url.value}")
			# print(f"[DEBUG] Updating widgets - mac: {config.plugins.stalkerportal.mac_address.value}")
			self["portal_input"].setText(config.plugins.stalkerportal.portal_url.value)
			self["mac_input"].setText(config.plugins.stalkerportal.mac_address.value)

			# Update playlist file display
			output_file = self.get_output_filename()
			# print(f"[DEBUG] Updating widgets - output_file: {output_file}")
			self["file_input"].setText(output_file)

			playlist_file = self.get_playlist_file_path()
			if hasattr(self, 'playlist_file') and self.playlist_file:
				playlist_file = self.playlist_file
			self["file_path_input"].setText(playlist_file)
			# print(f"[DEBUG] Updating widgets - playlist_file: {playlist_file}")

			self["key_green"].setText(self.get_convert_label())

			vod_status = _("VOD: ") + (_("Enabled") if config.plugins.stalkerportal.include_vod.value else _("Disabled"))
			self["account_info"].setText(vod_status)

			# Force GUI refresh
			self["portal_input"].instance.invalidate()
			self["mac_input"].instance.invalidate()
			self["file_input"].instance.invalidate()
			self["key_green"].instance.invalidate()
			portal = config.plugins.stalkerportal.portal_url.value
			mac = config.plugins.stalkerportal.mac_address.value

			if portal and mac:
				# print("[DEBUG] Triggering account info update")
				self.extract_account_info_async()

		except Exception as e:
			print(f"Error updating widgets: {str(e)}")
			self["status"].setText(_("Initialization error"))

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

				self.update_all_widgets()

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
		convert_type = config.plugins.stalkerportal.type_convert.value
		base_dir = config.plugins.stalkerportal.output_dir.value or defaultMoviePath()
		mac = config.plugins.stalkerportal.mac_address.value.replace(":", "_")

		if convert_type == "1":  # Convert to TV bouquet
			safe_name = self.get_safe_filename("Stalker Bouquet")  # Nome bouquet di default
			return f"/etc/enigma2/userbouquet.{safe_name}.tv"  # Percorso completo del bouquet
		
		# Resto del codice per M3U...
		# Solo append "movie" se non siamo già in una directory movie
		if not base_dir.endswith('/movie') and not base_dir.endswith('/movie/'):
			movie_dir = join(base_dir, 'movie')
			if not exists(movie_dir):
				try:
					makedirs(movie_dir, exist_ok=True)
				except:
					movie_dir = base_dir  # Fallback
			base_dir = movie_dir

		# Assicura trailing slash
		if not base_dir.endswith('/'):
			base_dir += '/'
		return f"{base_dir.rstrip('/')}/stalker_{mac}.m3u"

	def get_playlist_file_path(self):
		"""Get the full path to the playlist file"""
		input_filelist = config.plugins.stalkerportal.input_filelist.value
		if not input_filelist:
			input_filelist = '/etc/enigma2'  # defaultMoviePath()

		# Create directory if it doesn't exist
		if not exists(input_filelist):
			makedirs(input_filelist)

		return join(input_filelist, "stalker_playlist.txt")

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
		"""Validate MAC address format"""
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
		translations = {
			"Set Type Conversion": _("Set Type Conversion"),
			"Edit Portal URL": _("Edit Portal URL"),
			"Edit MAC Address": _("Edit MAC Address"),
			"Select Playlist File": _("Select Playlist File"),
			"Delete Playlist File": _("Delete Playlist File"),
			"Change Output Directory": _("Change Output Directory"),
			"Set Bouquet Position": _("Set Bouquet Position"),
			"Include VOD in Playlist": _("Include VOD in Playlist"),
			"Upgrade Stalker Converter": _("Upgrade Stalker Converter"),
			"Information": _("Information"),

		}

		menu_items = [
			("Set Type Conversion", self.select_type_convert),
			("Edit Portal URL", self.edit_portal),
			("Edit MAC Address", self.edit_mac),
			("Select Playlist File", self.select_playlist_file),
			("Delete Playlist File", self.delete_playlist),
			("Change Output Directory", self.select_output_dir),
			("Include VOD in Playlist", self.toggle_include_vod),  # Nuova opzione
			("Upgrade Stalker Converter", self.check_vers),
			("Information", self.show_info)
		]

		if config.plugins.stalkerportal.type_convert.value == "1":
			menu_items.insert(5, ("Set Bouquet Position", self.select_bouquet_position))

		menu = []
		for text, function in menu_items:
			translated = translations.get(text, text)
			menu.append((translated, function))

		self.session.openWithCallback(self.menu_callback, MenuDialog, menu)

	def toggle_include_vod(self):
		"""Toggle VOD inclusion in playlist"""
		# Inverti il valore corrente
		current_value = config.plugins.stalkerportal.include_vod.value
		config.plugins.stalkerportal.include_vod.value = not current_value

		# Salva la configurazione
		config.plugins.stalkerportal.include_vod.save()
		configfile.save()

		# Aggiorna lo stato
		status = _("Enabled") if config.plugins.stalkerportal.include_vod.value else _("Disabled")
		self["status"].setText(_("VOD inclusion: {}").format(status))

		# Ricarica l'interfaccia
		self.update_all_widgets()

	def edit_portal(self):
		"""
		Opens a virtual keyboard to edit the portal URL.
		When confirmed, updates the configuration and related UI elements.
		"""
		def portal_callback(portal):
			if portal:
				config.plugins.stalkerportal.portal_url.value = portal
				config.plugins.stalkerportal.portal_url.save()
				configfile.save()
				configfile.load()
				self.update_all_widgets()

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
				configfile.save()
				configfile.load()
				self.update_all_widgets()

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

				self.update_all_widgets()

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
				self.update_all_widgets()

		self.session.openWithCallback(
			bouquet_pos_callback,
			ChoiceBox,
			title=_("Select Bouquet Position"),
			list=[(key, _(val)) for key, val in options]
		)

	def load_playlist(self, file_path=None):
		"""Load playlist from selected file with robust parsing"""
		if not file_path:
			file_path = self.get_playlist_file_path()

		self.playlist_file = file_path
		self.portal_list = []
		valid_count = 0

		try:
			# Create file if it doesn't exist
			if not exists(file_path):
				with open(file_path, 'w') as f:
					f.write("")

			with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
				lines = [line.strip() for line in f.readlines()]

			current_portal = None
			for line in lines:
				if not line:
					continue

				# Portal line detection - handle various formats
				if any(keyword in line.lower() for keyword in ['panel', 'portal']) or line.startswith('http'):
					# Extract URL using more robust method
					url_match = search(r'(https?://[^\s]+)', line)
					if url_match:
						portal = url_match.group(0)
						# Normalize URL
						if not portal.startswith('http'):
							portal = 'http://' + portal
						if not portal.endswith('/c/'):
							portal = portal.rstrip('/') + '/c/'

						current_portal = portal

				# MAC line detection - handle various formats
				elif any(keyword in line.lower() for keyword in ['mac', 'mac_address']) or ':' in line:
					mac_match = search(r'([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})', line, IGNORECASE)
					if mac_match and current_portal:
						mac = mac_match.group(0)
						# Create entry for each portal-MAC combination
						display = f"{current_portal} - {mac}"
						self.portal_list.append((display, current_portal, mac))
						valid_count += 1

			# Update list display
			display_list = [entry[0] for entry in self.portal_list]
			self["file_list"].setList(display_list)

			if valid_count > 0:
				self["status"].setText(_("Loaded {} valid entries from {}").format(valid_count, basename(file_path)))
			else:
				self["status"].setText(_("No valid entries found in file"))

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

			# Add fallback if output_file is empty
			if not output_file:
				output_file = f"/tmp/stalker_{mac}.m3u"

			with open("/tmp/stalker_convert_info.log", "a") as f_debug:
				f_debug.write("=== convert_thread phase ===\n")

				# Step 1: Retrieve actual channel list
				self.update_status(_("Step 1/3: Connecting to portal..."))
				f_debug.write("=== Step 1/3: Connecting to portal... ===\n")
				success, token = self.get_channel_list(portal, mac)
				if self.conversion_stopped:
					self.finish_conversion(False, _("Conversion stopped during channel retrieval"))
					return

				if not success or not self.channels:
					self.finish_conversion(False, _("Failed to retrieve channel list"))
					return

				# Step 2: Create M3U content with actual channels
				self.update_status(_("Step 2/3: Creating playlist file..."))
				f_debug.write("=== Step 2/3: Creating playlist file... ===\n")
				convert_type = config.plugins.stalkerportal.type_convert.value
				f_debug.write("convert_type: " + convert_type + "\n")

				try:
					if convert_type == "0":
						self.update_status(_("Step 3/3: Create M3U file..."))
						f_debug.write("=== Step 3/3: Create M3U file... ===\n")
						with open(output_file, "w", encoding="utf-8") as f:
							f.write("#EXTM3U\n")
							f.write("# Portal: {}\n".format(portal))
							f.write("# MAC: {}\n".format(mac))
							f.write("# Channels: {}\n\n".format(len(self.channels)))

							for channel in self.channels:
								if self.conversion_stopped:
									f_debug.write("Conversion stopped detected in M3U creation loop\n")
									self.finish_conversion(False, _("Conversion stopped during M3U creation"))
									return

								cleaned_name = cleanName(channel["name"])
								cleaned_group = cleanName(channel["group"])

								f.write("#EXTINF:-1 tvg-id=\"{}\" tvg-name=\"{}\" ".format(
									channel["id"], cleaned_name))
								f.write("tvg-logo=\"{}\" group-title=\"{}\",{}\n".format(
									channel["logo"], cleaned_group, cleaned_name))
								f.write("{}\n\n".format(channel["url"]))

						# In convert_thread, modifica la chiamata a get_vod_list:
						if config.plugins.stalkerportal.include_vod.value:
							self.update_status(_("Step 4/4: Getting VOD content..."))

							# Passa il token ottenuto durante l'autenticazione
							vod_categories = self.get_vod_list(portal, mac, token)
							if vod_categories:
								# Aggiungi i VOD al file M3U
								with open(output_file, "a", encoding="utf-8") as f:
									f.write("\n\n#EXTM3U VOD SECTION\n\n")
									for category in vod_categories:
										f.write(f"#EXTINF:-1 group-title=\"VOD - {category['name']}\",{category['name']}\n")
										f.write("#EXTVLCOPT:network-caching=1000\n")
										f.write("\n")

										for movie in category['movies']:
											cleaned_name = cleanName(movie["name"])
											f.write(f"#EXTINF:-1 tvg-id=\"{movie['id']}\" ")
											f.write(f"tvg-name=\"{cleaned_name}\" ")
											f.write(f"group-title=\"VOD - {category['name']}\",")
											f.write(f"{cleaned_name}")

											if movie.get('year'):
												f.write(f" ({movie['year']})")

											f.write("\n")

											if movie.get('poster'):
												f.write(f"#EXTIMG:{movie['poster']}\n")

											f.write("#EXTVLCOPT:network-caching=1000\n")
											f.write(f"{movie['stream_url']}\n\n")

									total_vod = sum(len(cat['movies']) for cat in vod_categories)
									self.update_status(_("Added {} VOD items").format(total_vod))
									f_debug.write(f"Added {total_vod} VOD items\n")

						self.update_status(_("M3U created with {} channels").format(len(self.channels)))
						f_debug.write("M3U created with channels: " + str(len(self.channels)) + "\n")

					elif convert_type == "1":
						self.update_status(_("Step 3/3: Create Bouquet file..."))
						f_debug.write("=== Step 3/3: Create Bouquet file... ===\n")
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

						if config.plugins.stalkerportal.include_vod.value:
							self.update_status(_("Step 4/4: Getting VOD content..."))

							vod_categories = self.get_vod_list(portal, mac, token)

							if vod_categories:
								vod_group_names = []
								for category in vod_categories:
									group_name = "VOD: " + category['name']
									# Crea un bouquet separato per ogni categoria VOD
									self.write_group_bouquet(group_name, category['movies'], is_vod=True)
									vod_group_names.append(group_name)

								# Aggiungi i gruppi VOD alla lista generale
								for group_name in vod_group_names:
									groups[group_name] = []

								total_vod = sum(len(cat['movies']) for cat in vod_categories)
								self.update_status(_("Added {} VOD items").format(total_vod))
								f_debug.write(f"Added {total_vod} VOD items\n")

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

		finally:
			self.conversion_running = False
			self["key_green"].setText(self.get_convert_label())

	def write_group_bouquet(self, group, items, is_vod=False):
		"""Create .tv bouquet file per group, supporta VOD"""
		try:
			safe_name = self.get_safe_filename(group)
			filename = "/etc/enigma2/userbouquet." + safe_name + ".tv"
			temp_file = filename + ".tmp"

			with open(temp_file, "w", encoding="utf-8", errors="replace") as f:
				cleaned_group = cleanName(group) if group else safe_name
				f.write("#NAME {}\n".format(cleaned_group))

				if is_vod:
					f.write("#SERVICE 1:64:0:0:0:0:0:0:0:0:--- | VOD | ---\n")
					f.write("#DESCRIPTION --- | VOD | ---\n")
				else:
					f.write("#SERVICE 1:64:0:0:0:0:0:0:0:0:--- | Stalker2Bouquet | ---\n")
					f.write("#DESCRIPTION --- | Stalker2Bouquet | ---\n")

				for item in items:
					if is_vod:
						# Per i contenuti VOD
						url = item["stream_url"]
						name = item["name"]
					else:
						# Per i canali live
						url = item["url"]
						name = item["name"]

					if not url.startswith("http"):
						url = "http://" + url

					encoded_url = url.replace(":", "%3a")
					f.write("#SERVICE 4097:0:1:0:0:0:0:0:0:0:")
					f.write(encoded_url)
					f.write("\n")

					f.write("#DESCRIPTION ")
					f.write(name)
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
		# print(f"[DEBUG] Updating account info with text: {text}")
		from twisted.internet import reactor
		reactor.callFromThread(self._update_account_info_safe, text)

	def _update_account_info_safe(self, text):
		"""Update account info in main thread with debug"""
		# print(f"[DEBUG] Setting account_info widget to: {text}")
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
		token = None

		try:
			# Create main session with connection pooling
			session = requests.Session()

			# Configure advanced retry strategy
			retry_strategy = Retry(
				total=1,  # Increased total retries
				backoff_factor=0.5,  # More aggressive backoff
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
			response = session.get(link_api, params=params_handshake, headers=headers, timeout=5)
			token_match = search(r'"token"\s*:\s*"([^"]+)"', response.text)
			if not token_match:
				self.update_status(_("Failed to obtain token!"))
				return False

			token = token_match.group(1)

			# Step 2: Authentication
			self.update_status(_("Step 2/3: Authentication..."))

			headers["Authorization"] = f"Bearer {token}"
			params_auth = {"type": "stb", "action": "do_auth", "mac": mac, "token": token, "JsHttpRequest": "1-xml"}
			session.get(link_api, params=params_auth, headers=headers, timeout=5)

			# Step 3: Get channel list
			self.update_status(_("Step 3/3: Getting channels..."))
			params_channels = {"type": "itv", "action": "get_all_channels", "JsHttpRequest": "1-xml"}
			response = session.get(link_api, params=params_channels, headers=headers, timeout=10)
			json_data = response.json()
			channels_data = json_data.get("js", {}).get("data", [])
			total_channels = len(channels_data)

			if not channels_data:
				self.update_status(_("No channels found in response"))
				return False

			# Prepare for parallel processing
			self.update_status(_("Preparing %d channels...") % total_channels)
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
						eta = (total_channels - processed) / speed if speed > 0 else 0
						percent = (processed / total_channels * 100) if total_channels > 0 else 0

						self.update_status(
							_("Processed {}/{} channels ({:.1f}%)\nElapsed: {:.1f}s | Avg speed: {:.1f}/s | ETA: {:.1f}s").format(
								processed, total_channels, percent, elapsed, speed, eta
							)
						)
						last_update = current_time

			if self.conversion_stopped:
				return False

			elapsed = time.time() - start_time
			processed = len(self.channels)
			speed = processed / elapsed if elapsed > 0 else 0
			eta = (total_channels - processed) / speed if speed > 0 else 0
			percent = (processed / total_channels * 100) if total_channels > 0 else 0

			self.update_status(
				_("Processed {}/{} channels ({:.1f}%) | Elapsed: {:.1f}s | Avg speed: {:.1f}/s").format(
					processed, total_channels, percent, elapsed, speed
				)
			)

			return True, token

		except Exception as e:
			print('error stalker : ', e)
			# self.update_status(_("Channel processing error: ") + str(e))
			return False

	def get_vod_list(self, portal, mac, token):
		"""Retrieve VOD list with categories and movies - SIMPLIFIED VERSION"""
		vod_categories = []
		try:
			# 1. Get VOD categories
			categories_url = f"{portal}?type=vod&action=get_categories&JsHttpRequest=1-xml"
			categories_response = requests.get(categories_url, timeout=10)
			categories_data = categories_response.json().get("js", {}).get("data", [])
			
			# 2. Get movies for each category
			for category in categories_data:
				category_id = category.get("id")
				category_name = category.get("title", "Unknown Category")
				
				movies_url = f"{portal}?type=vod&action=get_ordered_list&genre={category_id}&JsHttpRequest=1-xml"
				movies_response = requests.get(movies_url, timeout=15)
				movies_data = movies_response.json().get("js", {}).get("data", [])
				
				vod_entries = []
				for movie in movies_data:
					# 3. Get stream URL for this movie
					cmd = movie.get("cmd")
					stream_url = self.get_vod_stream_url(portal, cmd, mac, token)
					
					vod_entries.append({
						"id": movie.get("id"),
						"name": movie.get("name", "Unknown Movie"),
						"year": movie.get("year", ""),
						"poster": movie.get("poster", ""),
						"stream_url": stream_url,
						"category": category_name
					})
				
				if vod_entries:
					vod_categories.append({
						"name": category_name,
						"movies": vod_entries
					})
			
			return vod_categories
			
		except Exception as e:
			print(f'Error getting VOD list: {str(e)}')
			return []

	def get_vod_stream_url(self, portal, cmd, mac, token):
		"""Retrieve actual stream URL for a VOD item"""
		try:
			# Extract host from portal URL
			parsed_portal = urlparse(portal)
			host = parsed_portal.netloc

			# Create headers with proper authentication
			timezone = fetch_system_timezone()
			headers = {
				"User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/537.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/537.3",
				"Accept": "*/*",
				"Accept-Encoding": "gzip, deflate",
				"Connection": "keep-alive",
				"Host": host,
				"Referer": portal,
				"Cookie": f"mac={mac}; stb_lang=en; timezone={timezone};",
				"X-User-Agent": "Model: MAG254; Link: Ethernet",
				"Authorization": f"Bearer {token}"
			}

			# Build API URL for VOD stream
			params = {
				"type": "vod",
				"action": "create_link",
				"cmd": cmd,
				"forced_storage": "undefined",
				"disable_ad": "0",
				"download": "0",
				"JsHttpRequest": "1-xml"
			}

			# Create session with retry logic
			session = requests.Session()
			retry_strategy = Retry(
				total=3,
				backoff_factor=0.5,
				status_forcelist=[429, 500, 502, 503, 504],
				allowed_methods=["GET"]
			)
			adapter = HTTPAdapter(max_retries=retry_strategy)
			session.mount("http://", adapter)
			session.mount("https://", adapter)

			# Make the request
			response = session.get(
				f"{portal.rstrip('/')}/portal.php",
				params=params,
				headers=headers,
				timeout=10
			)
			# Handle different response formats
			if response.status_code == 200:
				try:
					json_data = response.json()
					return json_data.get("js", {}).get("cmd", "")
				except JSONDecodeError:
					# Try to extract URL directly from text
					if response.text.startswith("http"):
						return response.text.strip()
					# Try to find URL in HTML response
					match = search(r'(https?://[^\s]+)', response.text)
					if match:
						return match.group(0)
			return ""

		except Exception as e:
			print(f'Error getting VOD stream URL: {str(e)}')
			return ""

	def get_vod_categories(self, portal, headers):
		"""Retrieve VOD categories from portal"""
		try:
			params = {
				"type": "vod",
				"action": "get_categories",
				"JsHttpRequest": "1-xml"
			}
			response = self.session.get(portal, params=params, headers=headers, timeout=10)
			return response.json().get("js", {}).get("data", [])
		except Exception as e:
			print(f"Error getting VOD categories: {str(e)}")
			return []

	def get_vod_movies(self, portal, category_id, headers):
		"""Retrieve movies for a specific VOD category"""
		try:
			params = {
				"type": "vod",
				"action": "get_ordered_list",
				"genre": category_id,
				"JsHttpRequest": "1-xml"
			}
			response = self.session.get(portal, params=params, headers=headers, timeout=15)
			return response.json().get("js", {}).get("data", [])
		except Exception as e:
			print(f"Error getting VOD movies: {str(e)}")
			return []

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
							total=1,
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
						else:
							# Alternative pattern for stalker portals
							cred_match = search(r'login=([^&]+)&password=([^&]+)', stream_url)
							if cred_match:
								username = cred_match.group(1)

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
					if isinstance(e.__cause__, ConnectionResetError) or "Connection reset by peer" in str(e):
						print(f"ConnectionResetError (errno 104) on channel '{channel_name}', skipping.")
						return None
					print("process_channel error attempt 1:", e)
					if attempt < max_retries:
						time.sleep(0.8 * (2 ** attempt))
						continue
					else:
						return None

				except Exception as e:
					if isinstance(e, ConnectionResetError) or "Connection reset by peer" in str(e):
						print(f"ConnectionResetError (errno 104) on channel '{channel_name}', skipping.")
						return None
					print("process_channel error attempt 2:", e)
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
		"""Select output directory using directory browser"""
		self.browse_directory(
			config.plugins.stalkerportal.output_dir.value,
			mode="directory"
		)

	def browse_directory(self, path=None, mode="playlist"):
		"""Browse files and directories with optional modes"""
		if path is None:
			path = config.plugins.stalkerportal.output_dir.value

		if not exists(path):
			try:
				makedirs(path, exist_ok=True)
			except:
				path = "/tmp"

		entries = []
		parent_dir = dirname(path)

		# Add parent directory navigation
		if parent_dir and parent_dir != path and exists(parent_dir):
			entries.append(("[DIR] ..", parent_dir, "dir"))

		# Add current directory selection option for directory mode
		if mode == "directory":
			entries.append(("[SELECT] " + _("Select this directory"), path, "select"))

		try:
			for item in sorted(listdir(path)):
				full_path = join(path, item)

				# Always show directories
				if isdir(full_path):
					entries.append((f"[DIR] {item}", full_path, "dir"))

				# Only show playlist files in playlist mode
				elif mode == "playlist" and item.lower().endswith(('.txt', '.list', '.m3u')):
					try:
						size = getsize(full_path)
						human_size = self.format_size(size)
						entries.append((f"{item} ({human_size})", full_path, "file"))
					except:
						entries.append((item, full_path, "file"))

		except Exception as e:
			self["status"].setText(_("Error accessing directory: ") + str(e))
			return

		if entries:
			entries.insert(0, (_("<< Cancel"), None, "cancel"))
			self.session.openWithCallback(
				lambda choice: self.handle_directory_selection(choice, mode),
				ChoiceBox,
				title=_("Select directory" if mode == "directory" else _("Select playlist file")),
				list=[(desc, (path, type)) for desc, path, type in entries]
			)
		else:
			self["status"].setText(_("No files found in: ") + path)

	def handle_directory_selection(self, choice, mode):
		"""Handle directory or file selection based on mode"""
		if not choice or not choice[1]:
			return

		selected_path, entry_type = choice[1]

		if entry_type == "cancel":
			return

		if entry_type == "dir":
			# Continue browsing in the same mode
			self.browse_directory(selected_path, mode)
			return

		if mode == "directory":
			if entry_type == "select":
				# Directory selected - set as output path
				print(f"[DEBUG] Selected directory: {selected_path}")
				config.plugins.stalkerportal.output_dir.value = selected_path
				config.plugins.stalkerportal.output_dir.save()
				configfile.save()
				configfile.load()
				# print(f"[DEBUG] Config saved: {config.plugins.stalkerportal.output_dir.value}")

				# Update UI immediately
				self.update_all_widgets()
				self["status"].setText(_("Selected directory: ") + selected_path)

		elif mode == "playlist" and entry_type == "file":
			# Playlist file selected
			print(f"[DEBUG] Selected playlist: {selected_path}")
			self.playlist_file = selected_path
			self.load_playlist(selected_path)
			config.plugins.stalkerportal.input_filelist.value = dirname(selected_path)
			config.plugins.stalkerportal.input_filelist.save()
			configfile.save()
			self["status"].setText(_("Loaded playlist: %s") % basename(selected_path))
			self.select_portal()

			# Update UI immediately
			self.update_all_widgets()

	def select_playlist_file(self):
		"""Select a playlist file - new function for yellow button"""
		self.browse_directory(
			config.plugins.stalkerportal.input_filelist.value,
			mode="playlist"
		)

	def delete_playlist(self, path=None):
		"""Browse files and directories to select for deletion"""
		if path is None:
			path = config.plugins.stalkerportal.input_filelist.value
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
		self["status"].setText(_("Fields cleared"))
		configfile.save()
		configfile.load()
		self.update_all_widgets()

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

	""" web portal server """

	def start_web_server(self):
		"""Start the web server with authentication"""
		if hasattr(self, 'web_server') and self.web_server:
			self.stop_web_server()
			self["key_web"].setText(_("Web Portal Server Off"))
			return

		try:
			port = 8080
			self.web_server = WebControlResource(self)
			self.site = server.Site(self.web_server)
			self.listening_port = reactor.listenTCP(port, self.site, interface="")
			ip_address = self.get_ip_address()
			self["key_web"].setText(_("Web Portal Server On"))
			self["status"].setText(_("Web server: http://%s:%d") % (ip_address, port))
		except Exception as e:
			self["status"].setText(_("Web server error: ") + str(e))

	def stop_web_server(self):
		"""Stop the web server"""
		if self.listening_port:
			self.listening_port.stopListening()
			self.listening_port = None
		self.web_server = None
		self["key_web"].setText(_("Web Portal Server Off"))
		self["status"].setText(_("Web server stopped"))

	def get_ip_address(self):
		"""Get device IP address"""
		try:
			s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
			s.connect(("8.8.8.8", 80))
			ip = s.getsockname()[0]
			s.close()
			return ip
		except:
			return "localhost"

	def update_config(self, portal, mac):
		config.plugins.stalkerportal.portal_url.value = portal
		config.plugins.stalkerportal.mac_address.value = mac
		configfile.save()
		configfile.load()
		self.update_all_widgets()
		self.extract_account_info_async()

	def close(self):
		self.stop_web_server()
		super().close()

	def check_for_updates(self):
		"""Check for updates from the web interface"""
		if exists(self.update_flag_file):
			try:
				# Read the type of update
				with open(self.update_flag_file, "r") as f:
					update_type = f.read().strip()

				# Remove the flag file
				remove(self.update_flag_file)

				# Perform the appropriate update
				if update_type == "full_reload":
					self.load_playlist(self.get_playlist_file_path())
				elif update_type == "config_update":
					self.update_all_widgets()
					self.extract_account_info_async()

			except Exception as e:
				print(f"Error processing update: {str(e)}")

	def generate_access_code(self):
		"""Generate a random 6-digit access code"""
		import random
		access_code = ''.join(str(random.randint(0, 9)) for _ in range(6))
		config.plugins.stalkerportal.web_access_code.value = access_code
		config.plugins.stalkerportal.web_access_code.save()
		configfile.save()
		return access_code

	def get_access_code(self):
		"""Returns the current access code"""
		if config.plugins.stalkerportal.web_access_code.value:
			return config.plugins.stalkerportal.web_access_code.value
		return self.generate_access_code()

	def get_masked_access_code(self):
		"""Returns the access code with a masked part"""
		code = self.get_access_code()
		if len(code) > 4:
			return code[:2] + "••" + code[-2:]
		return code

	def toggle_access_code_visibility(self):
		"""Show/hide full passcode"""
		if self.web_server:
			if self.access_code_visible:
				self.hide_access_code()
			else:
				self.show_access_code()
		else:
			self.session.open(
				MessageBox,
				_("You need to start the Web Server first!"),
				MessageBox.TYPE_INFO
			)

	def show_access_code(self):
		"""Show full code for 10 seconds"""
		self.access_code_visible = True
		self["access_code_label"].setText(_("Access Code: ") + self.get_access_code())
		self["show_code_btn"].setText(_("Hide Code"))

		# Start timer to auto hide after 10 seconds
		self.access_code_timer.start(10000, True)

	def hide_access_code(self):
		"""Hide full code"""
		self.access_code_visible = False
		self["access_code_label"].setText(_("Access Code: ") + self.get_masked_access_code())
		self["show_code_btn"].setText(_("Show Code"))
		self.access_code_timer.stop()

	def regenerate_access_code(self):
		"""Regenerate access code"""
		if self.web_server:
			new_code = self.generate_access_code()
			self.hide_access_code()

			# Show new code for 15 seconds
			self.access_code_visible = True
			self["access_code_label"].setText(_("New Access Code: ") + new_code)
			self.access_code_timer.start(15000, True)

			self.session.open(
				MessageBox,
				_("New access code generated!"),
				MessageBox.TYPE_INFO
			)
		else:
			self.session.open(
				MessageBox,
				_("You need to start the Web Server first!"),
				MessageBox.TYPE_INFO
			)

	def reset_access_code(self):
		"""Regenerate access code"""
		new_code = self.generate_access_code()
		self["access_code_label"].setText(_("Access Code: ") + self.get_masked_access_code())
		return new_code

	def get_current_portal(self):
		"""Returns the current portal URL from the configuration"""
		return config.plugins.stalkerportal.portal_url.value

	def get_current_mac(self):
		"""Returns the current MAC from the configuration"""
		return config.plugins.stalkerportal.mac_address.value

	def get_all_macs(self):
		"""Return a set of all MAC addresses currently in the playlist file."""
		macs = set()
		try:
			with open(self.get_playlist_file_path(), "r", encoding="utf-8") as f:
				lines = f.readlines()
			for line in lines:
				line = line.strip()
				if self.validate_mac_address(line):
					macs.add(line.upper())
		except FileNotFoundError:
			pass
		return macs

	def add_to_playlist(self, portal, macs):
		"""Add a new entry to the playlist, with duplicate MAC check"""
		mac_list = [m.strip() for m in macs.split(",") if m.strip()]

		if not portal or not mac_list:
			return "error", None

		# Get all existing MACs
		existing_macs = self.get_all_macs()

		# Check for duplicates
		for mac in mac_list:
			if mac.upper() in existing_macs:
				return "duplicate", mac

		try:
			with open(self.get_playlist_file_path(), "a", encoding="utf-8") as f:
				f.write("\n" + portal + "\n")
				for mac in mac_list:
					if self.validate_mac_address(mac):
						f.write(mac + "\n")

			# Aggiorna configurazione corrente
			config.plugins.stalkerportal.portal_url.value = portal
			config.plugins.stalkerportal.mac_address.value = mac_list[0]
			configfile.save()

			return "ok", None

		except Exception as e:
			print("Error adding to playlist:", str(e))
			return "error", None

	def get_full_playlist(self):
		"""Return the full playlist structure"""
		playlist = []
		if not hasattr(self, 'playlist_file') or not self.playlist_file:
			return playlist

		try:
			with open(self.playlist_file, 'r', encoding='utf-8') as f:
				lines = f.readlines()

			current_portal = None
			for line in lines:
				line = line.strip()
				if not line:
					continue

				# Portal line
				if line.startswith("http"):
					if current_portal:
						playlist.append(current_portal)
					current_portal = {'portal': line, 'macs': []}

				# MAC line
				elif self.validate_mac_address(line):
					if current_portal:
						current_portal['macs'].append(line)

			# Add last portal
			if current_portal:
				playlist.append(current_portal)

		except Exception as e:
			print(f"Error reading playlist: {str(e)}")

		return playlist

	def save_full_playlist(self, playlist):
		"""Save the entire playlist to the file"""
		try:
			with open(self.get_playlist_file_path(), 'w') as f:
				for entry in playlist:
					f.write(entry['portal'] + "\n")
					for mac in entry['macs']:
						f.write(mac + "\n")
					f.write("\n")

			self.playlist_file = self.get_playlist_file_path()
			self.load_playlist(self.playlist_file)
			return True
		except Exception as e:
			print(f"Error saving playlist: {str(e)}")
			return False

	def remove_entry(self, portal_index, mac_index=None):
		"""Remove an entry from the playlist"""
		playlist = self.get_full_playlist()

		if portal_index < 0 or portal_index >= len(playlist):
			return False

		if mac_index is not None:
			# Remove specific MAC
			if 0 <= mac_index < len(playlist[portal_index]['macs']):
				del playlist[portal_index]['macs'][mac_index]

				# Remove portal if no MACs left
				if not playlist[portal_index]['macs']:
					del playlist[portal_index]
		else:
			# Remove entire portal
			del playlist[portal_index]

		# Save updated playlist
		return self.save_playlist(playlist)

	def notify_plugin(self, update_type="full_reload"):
		"""Notify the plugin to reload"""
		try:
			# Directly use self.plugin (which is the StalkerPortalConverter instance)
			flag_file = self.update_flag_file
			with open(flag_file, "w") as f:
				f.write(update_type)
		except Exception as e:
			print(f"Error notifying plugin: {str(e)}")

	def update_entry(self, portal_index, new_portal, new_macs):
		"""Update an existing playlist entry"""
		playlist = self.get_full_playlist()

		if portal_index < 0 or portal_index >= len(playlist):
			return False

		playlist[portal_index]['portal'] = new_portal
		playlist[portal_index]['macs'] = [
			m.strip() for m in new_macs.split(',')
			if m.strip() and self.validate_mac_address(m.strip())
		]

		return self.save_playlist(playlist)

	def save_playlist(self, playlist):
		"""Save the playlist to file"""
		try:
			with open(self.playlist_file, 'w', encoding='utf-8') as f:
				for entry in playlist:
					f.write(f"{entry['portal']}\n")
					for mac in entry['macs']:
						f.write(f"{mac}\n")
					f.write("\n")

			# Reload playlist
			self.load_playlist(self.playlist_file)
			return True
		except Exception as e:
			print(f"Error saving playlist: {str(e)}")
			return False


class WebControlResource(resource.Resource):
	"""
	---
	**Web Interface Access and Management Help**

	This plugin includes a built-in web server that allows access to a full management interface.

	### How It Works

	* When the plugin starts, a **masked access code** is displayed (e.g., **"Access Code: 12••34"**).
	* Open the displayed web address in your browser (e.g., `http://192.168.1.100:8080`).
	* Enter the **full access code** shown in the plugin (e.g., `123456`).
	* If the entered code is correct, you gain access to the management interface.

	---

	### Authentication System Features

	* **Automatic code generation**:
	  A random 6-digit code is generated on plugin start (if not already present).
	  The code is stored in the plugin configuration.

	---

	### On-Screen Controls

	In the main screen:

	* You see **"Access Code: 12••34"** (yellow text).
	* Green button: **"Show Code"**
	* Red button: **"New Code"**
	* Below the buttons, you see hints: **"0-SHOW"**, **"1-NEW"**

	#### To reveal the full code:

	* Press **"0"** on the remote control.
	* The full code is shown (e.g., **"Access Code: 123456"**).
	* After 10 seconds, it returns to the masked view (e.g., **"12••34"**).

	#### To generate a new code:

	* Press **"1"** on the remote control.
	* A new 6-digit code is generated (e.g., `987654`).
	* You see **"New Access Code: 987654"** for 15 seconds.
	* Then it returns to masked view (e.g., **"98••54"**).

	---

	### Advanced Security

	* Access is **temporarily blocked after 3 failed attempts**.
	* **No hints** about the correct code are shown in error messages.
	* Code check is **case-insensitive**.

	---

	### Playlist Management Interface

	* View all saved portals and associated MAC addresses.
	* Displayed in a structured, tabular format with logical grouping.

	#### Available Actions:

	* ✏️ **Edit** a portal (URL and MAC list)
	* 🗑️ **Delete** an entire portal
	* 🗑️ **Delete** a single MAC address

	#### Confirmation & Feedback:

	* Confirmation popup for all delete operations
	* Visual feedback after each action

	---

	### Workflow

	* Full management access from the main menu
	* Inline editing with dedicated forms
	* Automatic return to main view after operations

	---

	### Real-Time Updates

	* Changes are saved directly to the playlist file
	* Plugin reloads automatically
	* Interface always reflects the current state

	---

	### How to Use

	#### Access Advanced Management:

	* From the homepage, click **"Full Management"**
	* Or go directly to `/manage` in the browser

	#### Edit an Entry:

	* Click the ✏️ icon next to a portal
	* Edit the URL and/or MAC list (comma-separated)
	* Click **"Save Changes"**

	#### Delete Items:

	* 🗑️ Next to a portal: deletes the entire portal
	* 🗑️ Next to a MAC: deletes only that MAC address

	#### Add New Entries:

	* Return to the homepage using the dedicated button
	* Use the main form to add new entries

	---

	### Security Notes

	* All operations require authentication
	* Input validation before saving:

	  * Correct URL format
	  * MAC address validation
	* Confirmation required for destructive actions
	* Clear error messages for invalid operations

	---
	"""

	isLeaf = True
	attempts = {}

	def __init__(self, plugin):
		resource.Resource.__init__(self)
		self.plugin = plugin
		self.playlist_path = self.plugin.get_playlist_file_path()
		self.sessions = {}

	def check_authentication(self, request):
		token = request.getCookie(b'session_token')
		# print("Session token from cookie:", token)
		if token:
			token = token.decode('utf-8')
			if token in self.sessions and self.sessions[token]['expires'] > time.time():
				print("Session valid.")
				return True
			else:
				print("Invalid or expired session.")
		return False

	def create_session(self, request):
		"""Create a new session for authenticated user"""
		token = urandom(16).hex()
		expires = time.time() + 3600  # 1 hour expiration
		self.sessions[token] = {
			'ip': request.getClientIP(),
			'expires': expires
		}
		request.setHeader(b'Set-Cookie', f"session_token={token}; Path=/; Max-Age=3600".encode('utf-8'))
		return token

	def is_ip_blocked(self, ip):
		"""Check if IP is temporarily blocked"""
		if ip in self.attempts:
			entry = self.attempts[ip]

			# If it's a block entry
			if 'blocked_until' in entry:
				if time.time() < entry['blocked_until']:
					return True
				else:
					del self.attempts[ip]  # Remove expired block
					return False

			# If it's just an attempt count entry
			return False
		return False

	def block_ip(self, ip):
		"""Block IP temporarily after too many failed attempts"""
		self.attempts[ip] = {
			'blocked_until': time.time() + 300,  # 5 minutes
			'type': 'block'
		}

	def render(self, request):
		"""Override render to add authentication check"""
		try:
			# Cleanup expired sessions first
			self.cleanup_sessions()

			path = request.path.decode("utf-8")
			client_ip = request.getClientIP()

			# Handle blocked IPs
			if self.is_ip_blocked(client_ip):
				return self.get_blocked_page()

			# Allow access to auth page without authentication
			if path == "/auth":
				return self.get_auth_page(request)
			elif path == "/":
				return self.get_index(request)

			# Check authentication for all other pages
			if not self.check_authentication(request):
				request.redirect(b"/auth")
				return b""

			if request.method == b"POST" and path == "/submit":
				return self.render_POST(request)

			# Handle authenticated requests
			try:
				if path == "/":
					return self.get_index(request)
				elif path == "/manage":
					return self.get_manage_page()
				elif path.startswith("/edit/"):
					parts = path.split("/")
					portal_index = int(parts[2])
					return self.get_edit_page(portal_index)
				elif path.startswith("/delete/"):
					parts = path.split("/")
					portal_index = int(parts[2])
					mac_index = int(parts[3]) if len(parts) > 3 else None
					return self.delete_entry(portal_index, mac_index, request)
				elif path == "/upload":
					return self.get_upload_page()
				elif path == "/upload_file":
					return self.handle_file_upload(request)
				elif path.startswith("/save_edit/"):
					parts = path.split("/")
					portal_index = int(parts[2])
					return self.save_edit(portal_index, request)
				return self.get_404_page()
			except Exception as e:
				return self.get_error_page(str(e))
		except Exception as e:
			return self.get_error_page(f"Internal server error: {str(e)}")

	def get_auth_page(self, request):
		"""Authentication page"""
		message = ""
		client_ip = request.getClientIP()

		# Check if IP is blocked
		if self.is_ip_blocked(client_ip):
			return self.get_blocked_page()

		if request.method == b'POST':

			submitted_code = request.args.get(b'access_code', [b''])[0].decode('utf-8').strip()
			correct_code = self.plugin.get_access_code().strip()
			# print("Submitted:", submitted_code)
			# print("Correct  :", correct_code)
			if submitted_code == correct_code:
				self.create_session(request)
				html = """
				<html>
					<head>
						<meta http-equiv="refresh" content="0; url=/" />
					</head>
					<body>
						<p>Redirecting...</p>
					</body>
				</html>
				"""
				return html.encode("utf-8")
			else:
				# Track failed attempts
				if client_ip not in self.attempts:
					self.attempts[client_ip] = {'count': 1}
				else:
					self.attempts[client_ip]['count'] += 1

					# Block after 3 failed attempts
					if self.attempts[client_ip]['count'] >= 3:
						self.block_ip(client_ip)
						return self.get_blocked_page()

				message = "<div class='error-message'>Invalid access code! Attempts left: {}</div>".format(
					3 - self.attempts[client_ip]['count']
				)

		html = f"""
		<!DOCTYPE html>
		<html lang="en">
		<head>
			<meta charset="UTF-8">
			<meta name="viewport" content="width=device-width, initial-scale=1.0">
			<title>Authentication Required</title>
			{self.get_css()}
		</head>
		<body>
			<div class="container">
				<header>
					<h1>Authentication Required</h1>
					<p class="subtitle">Enter access code from your device</p>
				</header>

				<div class="card">
					<h2 class="card-title">Access Control</h2>

					{message}

					<form method="POST">
						<div class="form-group">
							<label for="access_code">Access Code:</label>
							<input type="password" id="access_code" name="access_code"
								   placeholder="Enter 6-digit code" required autofocus>
						</div>
						<button type="submit">Authenticate</button>
					</form>

					<div class="instructions">
						<h3>How to get the code:</h3>
						<ul>
							<li>On your device, go to the Stalker Portal Converter plugin</li>
							<li>Look for the <strong>Access Code</strong> in the interface</li>
							<li>If needed, press <strong>0</strong> to reveal the full code</li>
							<li>Enter the code above to gain access</li>
						</ul>
					</div>
				</div>
			</div>
			{self.get_footer()}
		</body>
		</html>
		"""
		return html.encode('utf-8')

	def get_blocked_page(self):
		"""IP blocked page"""
		html = f"""
		<!DOCTYPE html>
		<html lang="en">
		<head>
			<meta charset="UTF-8">
			<meta name="viewport" content="width=device-width, initial-scale=1.0">
			<title>Access Temporarily Blocked</title>
			{self.get_css()}
		</head>
		<body>
			<div class="container">
				<header>
					<h1>Access Temporarily Blocked</h1>
				</header>

				<div class="card">
					<div class="error-message">
						⚠️ Too many failed authentication attempts
					</div>

					<p>Your IP address has been temporarily blocked for security reasons.</p>
					<p>Please try again in 5 minutes or restart your router to get a new IP address.</p>

					<div class="instructions">
						<h3>Security Notice:</h3>
						<ul>
							<li>This system automatically blocks IP addresses after 3 failed attempts</li>
							<li>The block will be automatically removed after 5 minutes</li>
							<li>For assistance, please contact your system administrator</li>
						</ul>
					</div>
				</div>
			</div>
			{self.get_footer()}
		</body>
		</html>
		"""
		return html.format(self.get_css()).encode('utf-8')

	def get_css(self):
		"""Return the dark theme CSS compatible with Enigma2"""
		return """
		<style>
			:root {
				--primary: #00a8ff;
				--secondary: #2c3e50;
				--success: #27ae60;
				--danger: #e74c3c;
				--warning: #f39c12;
				--dark: #1a1d21;
				--darker: #15181c;
				--light: #ecf0f1;
				--text: #e0e0e0;
			}

			* {
				box-sizing: border-box;
				margin: 0;
				padding: 0;
			}

			body {
				font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
				line-height: 1.6;
				color: var(--text);
				background: linear-gradient(135deg, var(--darker) 0%, var(--dark) 100%);
				padding: 20px;
				min-height: 100vh;
			}

			.container {
				max-width: 900px;
				margin: 0 auto;
				background: rgba(30, 33, 38, 0.85);
				padding: 25px;
				border-radius: 12px;
				box-shadow: 0 5px 25px rgba(0, 0, 0, 0.5);
				border: 1px solid rgba(80, 85, 90, 0.3);
			}

			header {
				text-align: center;
				margin-bottom: 25px;
				padding-bottom: 20px;
				border-bottom: 1px solid rgba(80, 85, 90, 0.3);
			}

			h1 {
				color: var(--primary);
				margin-bottom: 10px;
				font-size: 2.2rem;
				text-shadow: 0 2px 4px rgba(0, 0, 0, 0.3);
			}

			.subtitle {
				color: #aaa;
				font-size: 1.1rem;
			}

			.card {
				background: rgba(40, 44, 50, 0.8);
				border-radius: 10px;
				padding: 25px;
				margin-bottom: 25px;
				border: 1px solid rgba(70, 75, 80, 0.3);
				box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
			}

			.card-title {
				font-size: 1.4rem;
				color: var(--primary);
				margin-bottom: 20px;
				display: flex;
				align-items: center;
				padding-bottom: 10px;
				border-bottom: 1px solid rgba(70, 75, 80, 0.3);
			}

			.card-title i {
				margin-right: 10px;
			}

			.form-group {
				margin-bottom: 20px;
			}

			label {
				display: block;
				margin-bottom: 8px;
				font-weight: 600;
				color: #ccc;
			}

			input[type="text"], textarea, input[type="file"] {
				width: 100%;
				padding: 12px 15px;
				background: rgba(30, 33, 38, 0.8);
				border: 1px solid #444;
				border-radius: 6px;
				font-size: 16px;
				transition: all 0.3s;
				color: var(--text);
			}

			input[type="text"]:focus, textarea:focus {
				border-color: var(--primary);
				outline: none;
				box-shadow: 0 0 0 3px rgba(0, 168, 255, 0.2);
			}

			button, .btn {
				display: inline-block;
				padding: 12px 25px;
				background: var(--primary);
				color: white;
				border: none;
				border-radius: 6px;
				cursor: pointer;
				font-size: 16px;
				font-weight: 600;
				transition: all 0.3s;
				text-decoration: none;
				text-align: center;
			}

			.btn-save {
				background-color: #28a745; /* verde */
				color: white;
				border: none;
				padding: 10px 20px;
				border-radius: 4px;
				transition: background 0.3s ease, transform 0.3s ease, box-shadow 0.3s ease;
			}

			.btn-save:hover {
				background-color: #0090e0; /* blu, come definito nel tuo hover */
				transform: translateY(-2px);
				box-shadow: 0 4px 8px rgba(0, 0, 0, 0.3);
				color: white;
			}

			button:hover, .btn:hover {
				background: #0090e0;
				transform: translateY(-2px);
				box-shadow: 0 4px 8px rgba(0, 0, 0, 0.3);
			}

			.btn-secondary {
				background: var(--secondary);
			}

			.btn-secondary:hover {
				background: #233140;
			}

			.btn-success {
				background: var(--success);
			}

			.btn-success:hover {
				background: #219653;
			}

			.btn-danger {
				background: var(--danger);
			}

			.btn-danger:hover {
				background: #c0392b;
			}

			.btn-warning {
				background: var(--warning);
			}

			.btn-warning:hover {
				background: #d35400;
			}

			.actions {
				display: flex;
				gap: 15px;
				margin-top: 20px;
			}

			.actions a {
				flex: 1;
				text-align: center;
			}

			.current-values {
				background: rgba(30, 33, 38, 0.6);
				padding: 20px;
				border-radius: 8px;
				margin-bottom: 25px;
				border-left: 4px solid var(--primary);
			}

			.current-values p {
				margin-bottom: 8px;
			}

			.current-values strong {
				color: var(--primary);
			}

			.message-box {
				padding: 15px;
				border-radius: 6px;
				margin: 15px 0;
				text-align: center;
			}

			.success-message {
				background: rgba(39, 174, 96, 0.2);
				border: 1px solid var(--success);
				color: #d4ffd4;
			}

			.error-message {
				background: rgba(231, 76, 60, 0.2);
				border: 1px solid var(--danger);
				color: #ffd4d4;
			}

			.warning-message {
				background: rgba(243, 156, 18, 0.2);
				border: 1px solid var(--warning);
				color: #fff8d4;
			}

			table {
				width: 100%;
				border-collapse: collapse;
				margin: 25px 0;
				background: rgba(30, 33, 38, 0.6);
				border-radius: 8px;
				overflow: hidden;
			}

			th, td {
				padding: 15px;
				text-align: left;
				border-bottom: 1px solid rgba(70, 75, 80, 0.3);
			}

			th {
				background-color: var(--secondary);
				color: white;
				font-weight: 600;
			}

			tr:hover {
				background-color: rgba(50, 55, 60, 0.5);
			}

			.actions-cell {
				text-align: center;
				width: 200px;
			}

			.action-btn {
				display: inline-block;
				padding: 8px 16px;
				border-radius: 4px;
				color: white;
				text-decoration: none;
				margin: 0 5px;
				font-size: 14px;
				font-weight: 600;
				transition: all 0.3s;
			}

			.edit-btn {
				background: var(--primary);
			}

			.edit-btn:hover {
				background: #0090e0;
			}

			.delete-btn {
				background: var(--danger);
			}

			.delete-btn:hover {
				background: #c0392b;
			}

			.back-link {
				display: inline-block;
				margin-bottom: 20px;
				text-decoration: none;
				color: var(--primary);
				font-weight: 600;
				padding: 8px 15px;
				border-radius: 4px;
				background: rgba(0, 168, 255, 0.1);
				transition: all 0.3s;
			}

			.back-link:hover {
				background: rgba(0, 168, 255, 0.2);
				text-decoration: none;
			}

			.mac-list {
				list-style-type: none;
				padding: 0;
			}

			.mac-list li {
				padding: 10px 0;
				border-bottom: 1px solid rgba(70, 75, 80, 0.3);
				display: flex;
				justify-content: space-between;
				align-items: center;
			}

			.mac-list li:last-child {
				border-bottom: none;
			}

			.instructions {
				background: rgba(40, 44, 50, 0.6);
				padding: 20px;
				border-radius: 8px;
				margin: 25px 0;
				border-left: 4px solid var(--warning);
			}

			.instructions h3 {
				margin-bottom: 15px;
				color: var(--warning);
			}

			.instructions ul {
				padding-left: 20px;
			}

			.instructions li {
				margin-bottom: 10px;
				color: #ccc;
			}

			.file-input-wrapper {
				position: relative;
				overflow: hidden;
				display: inline-block;
				width: 100%;
			}

			.file-input-wrapper input[type="file"] {
				position: absolute;
				left: 0;
				top: 0;
				opacity: 0;
				width: 100%;
				height: 100%;
				cursor: pointer;
			}

			.file-input-label {
				display: block;
				padding: 12px;
				background: rgba(30, 33, 38, 0.8);
				border: 1px dashed #555;
				border-radius: 6px;
				text-align: center;
				color: #aaa;
				cursor: pointer;
				transition: all 0.3s;
			}

			.file-input-label:hover {
				border-color: var(--primary);
				color: var(--primary);
				background: rgba(0, 168, 255, 0.1);
			}

			.upload-icon {
				font-size: 24px;
				margin-bottom: 10px;
				display: block;
				color: var(--primary);
			}
		</style>
		"""

	def get_index(self, request):
		"""Main page with portal and MAC form"""
		current_portal = self.plugin.get_current_portal()
		current_mac = self.plugin.get_current_mac()
		saved_message = ""

		if b"saved" in request.args:
			saved_message = """
			<div class="message-box success-message">
				✓ Playlist entry saved successfully
			</div>
			"""

		html = f"""
		<!DOCTYPE html>
		<html lang="en">
		<head>
			<meta charset="UTF-8">
			<meta name="viewport" content="width=device-width, initial-scale=1.0">
			<title>Stalker Portal Converter</title>
			{self.get_css()}
		</head>
		<body>
			<div class="container">
				<header>
					<h1>Stalker Portal Converter</h1>
					<h1>by Lululla</h1>
					<p class="subtitle">Manage your IPTV portals and MAC addresses</p>
				</header>
				{saved_message}
				<div class="card">
					<h2 class="card-title">Current Configuration</h2>
					<div class="current-values">
						<p><strong>Portal URL:</strong> {current_portal}</p>
						<p><strong>MAC Address:</strong> {current_mac}</p>
					</div>
					<h2 class="card-title">Add New Entry</h2>
					<form method="POST" action="/submit">
						<div class="form-group">
							<label for="portal">Portal URL:</label>
							<input type="text" id="portal" name="portal"
								   value="{current_portal}"
								   placeholder="http://example.com:80/c/" required>
						</div>
						<div class="form-group">
							<label for="mac">MAC Addresses (comma separated):</label>
							<textarea id="mac" name="mac" rows="3"
									  placeholder="00:1A:79:XX:XX:XX, 00:1B:78:YY:YY:YY"
									  required>{current_mac}</textarea>
							<small>Enter multiple MAC addresses separated by commas</small>
						</div>
						<button type="submit" class="btn-save">Save Configuration</button>
					</form>
					<div class="actions">
						<a href="/manage" class="btn btn-secondary">Full Management</a>
						<a href="/upload" class="btn btn-warning">Upload Playlist</a>
						<a href="/manage" class="btn btn-primary">View Playlist</a>
					</div>
				</div>
				<div class="instructions">
					<h3>How to Use:</h3>
					<ul>
						<li><strong>Add New Entry</strong>: Enter portal URL and MAC addresses</li>
						<li><strong>Full Management</strong>: View, edit, or delete all entries</li>
						<li><strong>Upload Playlist</strong>: Load playlist from your computer</li>
						<li><strong>Edit</strong>: Click the [Edit] button next to a portal</li>
						<li><strong>Delete</strong>: Click the [Delete] button to remove items</li>
					</ul>
				</div>
			</div>
			{self.get_footer()}
		</body>
		</html>
		"""
		return html.encode("utf-8")

	def get_manage_page(self):
		"""Full playlist management page"""
		playlist = self.plugin.get_full_playlist()
		if not playlist:
			try:
				self.plugin.load_playlist(self.playlist_path)
				playlist = self.plugin.get_full_playlist()
			except Exception as e:
				print("[ERROR] Failed to load playlist: " + str(e))

		file_info = "Playlist: " + basename(self.playlist_path)
		# Generate table rows
		rows = ""
		for idx, entry in enumerate(playlist):
			portal = entry['portal']
			macs = entry.get('macs', [])

			# Create a row for each MAC
			for mac_idx, mac in enumerate(macs):
				rows += f"""
				<tr>
					<td class="actions-cell">
						<a href="/edit/{idx}/{mac_idx}" class="action-btn edit-btn">Edit</a>
						<a href="/delete/{idx}/{mac_idx}" class="action-btn delete-btn"
						   onclick="return confirm('Delete this entry?')">Delete</a>
					</td>
					<td>{portal}</td>
					<td>{mac}</td>
				</tr>
				"""

		html = f"""
		<!DOCTYPE html>
		<html lang="en">
		<head>
			<meta charset="UTF-8">
			<meta name="viewport" content="width=device-width, initial-scale=1.0">
			<title>Playlist Management - Stalker Portal Converter</title>
			{self.get_css()}
		</head>
		<body>
			<div class="container">
				<header>
					<h1>Playlist Management</h1>
					<p class="subtitle">{file_info}</p>
				</header>

				<a href="/" class="back-link">← Back to Main</a>

				<!-- DEBUG: Show playlist content -->
				<div class="debug-info" style="display: none; background: rgba(255,0,0,0.1); padding: 10px; margin: 10px 0;">
					<strong>Debug Info:</strong>
					<pre>{dumps(playlist, indent=2)}</pre>
				</div>
				<div class="card">
					<h2 class="card-title">All Entries</h2>

					{"<p class='message-box warning-message'>No entries found in playlist</p>" if not playlist else ""}

					<table>
						<thead>
							<tr>
								<th width="220">Actions</th>
								<th>Portal URL</th>
								<th>MAC Address</th>
							</tr>
						</thead>
						<tbody>
							{rows if playlist else "<tr><td colspan='3' style='text-align: center;'>No entries found</td></tr>"}
						</tbody>
					</table>

					<div class="actions">
						<a href="/" class="btn btn-secondary">Add New Entry</a>
						<a href="/upload" class="btn btn-warning">Upload Playlist</a>
					</div>
				</div>
			</div>
			{self.get_footer()}
		</body>
		</html>
		"""
		return html.encode('utf-8')

	def get_edit_page(self, portal_index):
		"""Edit page for a portal entry"""
		playlist = self.plugin.get_full_playlist()
		if portal_index < 0 or portal_index >= len(playlist):
			return self.get_404_page("Invalid portal index")
		entry = playlist[portal_index]
		portal = entry['portal']
		macs = ", ".join(entry['macs'])

		html = f"""
		<!DOCTYPE html>
		<html lang="en">
		<head>
			<meta charset="UTF-8">
			<meta name="viewport" content="width=device-width, initial-scale=1.0">
			<title>Edit Portal - Stalker Portal Converter</title>
			{self.get_css()}
		</head>
		<body>
			<div class="container">
				<header>
					<h1>Edit Portal</h1>
					<p class="subtitle">Modify portal URL and MAC addresses</p>
				</header>
				<a href="/manage" class="back-link">← Back to Management</a>
				<div class="card">
					<form method="POST" action="/save_edit/{portal_index}">
						<div class="form-group">
							<label for="portal">Portal URL:</label>
							<input type="text" id="portal" name="portal"
								   value="{portal}"
								   placeholder="http://example.com:80/c/" required>
						</div>
						<div class="form-group">
							<label for="mac">MAC Addresses (comma separated):</label>
							<textarea id="mac" name="mac" rows="5"
									  placeholder="00:1A:79:XX:XX:XX, 00:1B:78:YY:YY:YY"
									  required>{macs}</textarea>
							<small>Enter multiple MAC addresses separated by commas</small>
						</div>
						<div class="actions">
							<a href="/manage" class="btn btn-secondary">Cancel</a>
							<button type="submit" class="btn btn-success">Save Changes</button>
						</div>
					</form>

				</div>
			</div>
			{self.get_footer()}
		</body>
		</html>
		"""
		return html.encode('utf-8')

	def delete_entry(self, portal_index, mac_index, request):
		"""Delete a portal or MAC entry"""
		if self.plugin.remove_entry(portal_index, mac_index):
			self.plugin.notify_plugin("full_reload")
			request.redirect(b"/manage")
			return b""
		else:
			return self.get_error_page("Failed to delete entry")

	def save_edit(self, portal_index, request):
		"""Save changes to a portal entry"""
		portal = request.args.get(b'portal', [b''])[0].decode('utf-8')
		macs = request.args.get(b'mac', [b''])[0].decode('utf-8')

		if self.plugin.update_entry(portal_index, portal, macs):
			self.plugin.notify_plugin("full_reload")
			request.redirect(b"/manage")
			return b""
		else:
			return self.get_error_page("Failed to save changes")

	def get_upload_page(self):
		"""Playlist upload page"""
		html = f"""
		<!DOCTYPE html>
		<html lang="en">
		<head>
			<meta charset="UTF-8">
			<meta name="viewport" content="width=device-width, initial-scale=1.0">
			<title>Upload Playlist - Stalker Portal Converter</title>
			{self.get_css()}
		</head>
		<body>
			<div class="container">
				<header>
					<h1>Upload Playlist</h1>
					<p class="subtitle">Load playlist from your computer</p>
				</header>
				<a href="/" class="back-link">← Back to Main</a>
				<div class="card">
					<h2 class="card-title">Upload Playlist File</h2>
					<form method="POST" action="/upload_file" enctype="multipart/form-data">
						<div class="form-group">
							<label>Select playlist file (TXT):</label>
							<div class="file-input-wrapper">
								<input type="file" id="playlist_file" name="playlist_file" accept=".txt" required>
								<label for="playlist_file" class="file-input-label">
									<span class="upload-icon">📁</span>
									<span>Click to select playlist file</span>
								</label>
							</div>
							<p class="current-values" style="margin-top: 10px;">
								<strong>Current playlist:</strong> {basename(self.playlist_path)}
							</p>
						</div>

						<div class="instructions">
							<h3>Instructions:</h3>
							<ul>
								<li>Playlist file should contain portal URLs and MAC addresses</li>
								<li>Supported format: TXT files</li>
								<li>Example format:
									<pre>http://portal1.com:80/c/
00:1A:79:AA:BB:CC
http://portal2.com:8080/c/
00:1B:78:DD:EE:FF</pre>
								</li>
							</ul>
						</div>
						<button type="submit">Upload Playlist</button>
					</form>
				</div>
			</div>
			{self.get_footer()}
		</body>
		</html>
		"""
		return html.encode('utf-8')

	def handle_file_upload(self, request):
		"""Handle playlist file upload"""
		try:
			# Reload playlist in plugin
			self.plugin.load_playlist(self.playlist_path)
			# Notify plugin to reload
			self.plugin.notify_plugin("full_reload")

			html = f"""
			<!DOCTYPE html>
			<html lang="en">
			<head>
				<meta charset="UTF-8">
				<meta name="viewport" content="width=device-width, initial-scale=1.0">
				<title>Upload Successful - Stalker Portal Converter</title>
				{self.get_css()}
			</head>
			<body>
				<div class="container">
					<header>
						<h1>Playlist Uploaded</h1>
					</header>

					<a href="/" class="back-link">← Back to Main</a>

					<div class="card">
						<div class="message-box success-message">
							✓ Playlist file uploaded successfully
						</div>
						<div class="actions">
							<a href="/" class="btn btn-secondary">Go to Main</a>
							<a href="/manage" class="btn btn-primary">View Playlist</a>
						</div>
					</div>
				</div>
				{self.get_footer()}
			</body>
			</html>
			"""
			return html.encode('utf-8')

		except Exception as e:
			return self.get_error_page(f"Error uploading file: {str(e)}")

	def get_404_page(self, message="Page not found"):
		"""404 error page"""
		html = f"""
		<!DOCTYPE html>
		<html lang="en">
		<head>
			<meta charset="UTF-8">
			<meta name="viewport" content="width=device-width, initial-scale=1.0">
			<title>Page Not Found - Stalker Portal Converter</title>
			<style>
				/* All styles from index page */
			</style>
		</head>
		<body>
			<div class="container">
				<header>
					<h1>Page Not Found</h1>
				</header>

				<div class="card">
					<div class="error-message">
						{message}
					</div>

					<p>The page you requested could not be found.</p>

					<div class="actions">
						<a href="/" class="btn btn-primary">Return to Main</a>
					</div>
				</div>
			</div>
			{self.get_footer()}
		</body>
		</html>
		"""
		return html.encode('utf-8')

	def get_error_page(self, error):
		"""Error page"""
		html = f"""
		<!DOCTYPE html>
		<html lang="en">
		<head>
			<meta charset="UTF-8">
			<meta name="viewport" content="width=device-width, initial-scale=1.0">
			<title>Error - Stalker Portal Converter</title>
			<style>
				/* All styles from index page */
			</style>
		</head>
		<body>
			<div class="container">
				<header>
					<h1>An Error Occurred</h1>
				</header>

				<div class="card">
					<div class="error-message">
						{error}
					</div>

					<p>Please try again or contact support if the problem persists.</p>

					<div class="actions">
						<a href="/" class="btn btn-primary">Return to Main</a>
						<a href="/manage" class="btn btn-secondary">Back to Management</a>
					</div>
				</div>
			</div>
			{self.get_footer()}
		</body>
		</html>
		"""
		return html.encode('utf-8')

	def cleanup_sessions(self):
		"""Remove expired sessions"""
		now = time.time()
		expired_tokens = [token for token, data in self.sessions.items() if data['expires'] < now]
		for token in expired_tokens:
			del self.sessions[token]

	def render_GET(self, request):
		path = request.path.decode("utf-8")
		parts = path.strip("/").split("/")

		try:
			if path == "/":
				return self.get_index(request)

			elif path == "/manage":
				return self.get_manage_page()

			elif parts[0] == "edit" and len(parts) == 2:
				portal_index = int(parts[1])
				return self.get_edit_page(portal_index)

			elif parts[0] == "delete" and len(parts) >= 3:
				portal_index = int(parts[1])
				mac_index = int(parts[2])
				return self.delete_entry(portal_index, mac_index, request)

			elif path == "/upload":
				return self.get_upload_page()

			elif path == "/upload_file":
				return self.handle_file_upload(request)

			elif parts[0] == "save_edit" and len(parts) == 2:
				portal_index = int(parts[1])
				return self.save_edit(portal_index, request)

			else:
				return self.get_404_page()
		except Exception as e:
			return self.get_error_page(str(e))

	def render_POST(self, request):
		path = request.path.decode("utf-8")

		if path == "/submit":
			portal = request.args.get(b"portal", [b""])[0].decode("utf-8").strip()
			macs = request.args.get(b"mac", [b""])[0].decode("utf-8").strip()

			if not portal or not macs:
				return self.get_error_page("Portal URL and MAC addresses are required.")

			success, message = self.plugin.add_to_playlist(portal, macs)

			if not success:
				return self.get_error_page(message)

			self.plugin.notify_plugin("config_update")
			request.redirect(b"/?saved=1")
			return b""

		elif path == "/save_playlist_entry":
			# Handle JSON API save request
			content = request.content.read().decode("utf-8")
			try:
				data = loads(content)
				portal = data.get("portal", "").strip()
				macs = data.get("macs", "").strip()
				confirm = data.get("confirm", False)
			except Exception:
				request.setResponseCode(400)
				return b"Invalid JSON data"

			if not confirm:
				response = {
					"message": "Confirm saving playlist entry?",
					"portal": portal,
					"macs": macs
				}
				request.setHeader("Content-Type", "application/json")
				return dumps(response).encode("utf-8")

			if self.plugin.add_to_playlist(portal, macs):
				self.plugin.load_playlist(self.playlist_path)  # Load updated playlist into memory
				self.plugin.notify_plugin("config_update")
				request.setHeader("Content-Type", "text/plain")
				return b"Playlist saved successfully"
			else:
				request.setResponseCode(500)
				return b"Failed to save playlist entry"

		else:
			request.setResponseCode(404)
			return b"Not found"

	def get_footer(self):
		return """
		<footer style="text-align:center; margin:20px 0; color:#888; font-size:0.9em;">
			powered by Lululla
		</footer>
		"""


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
