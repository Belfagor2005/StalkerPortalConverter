# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

"""
#########################################################
#                                                       #
#  Stalker Portal Converter Plugin                      #
#  Version: 1.2                                         #
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

import queue
import threading
import time
from os import (
	listdir,
	makedirs,
	statvfs,
	remove,
	access,
	W_OK,
	chmod
)
from os.path import (
	basename,
	dirname,
	exists,
	isdir,
	isfile,
	join,
	getsize,

)
from re import IGNORECASE, compile, search
from urllib.parse import urlencode, urlparse
# from enigma import eTimer

import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MenuList import MenuList
from Components.config import config, ConfigSelection, ConfigSubsection, ConfigText

from Plugins.Plugin import PluginDescriptor

from Screens.ChoiceBox import ChoiceBox
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from Screens.VirtualKeyBoard import VirtualKeyBoard

from Tools.Directories import resolveFilename, SCOPE_MEDIA, defaultRecordingLocation

from . import _

currversion = '1.2'

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


def get_mounted_devices():
	"""Recovers mounted devices with write permissions"""
	from Components.Harddisk import harddiskmanager

	devices = [
		(resolveFilename(SCOPE_MEDIA, "hdd"), _("Hard Disk")),
		(resolveFilename(SCOPE_MEDIA, "usb"), _("USB Drive"))
	]

	devices.append(("/tmp/", _("Temporary Storage")))

	try:
		devices += [
			(p.mountpoint, p.description or _("Disk"))
			for p in harddiskmanager.getMountedPartitions()
			if p.mountpoint and access(p.mountpoint, W_OK)
		]

		net_dir = resolveFilename(SCOPE_MEDIA, "net")
		if isdir(net_dir):
			devices += [(join(net_dir, d), _("Network")) for d in listdir(net_dir)]

	except Exception as e:
		print("ERROR Mount error: %s" % str(e))

	unique_devices = {}
	for p, d in devices:
		path = p.rstrip("/") + "/"
		if isdir(path):
			unique_devices[path] = d

	return list(unique_devices.items())


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
config.plugins.stalkerportal.portal_url = ConfigText(default="http://my.server.xyz:8080/c/", fixed_size=False)
config.plugins.stalkerportal.mac_address = ConfigText(default="00:1A:79:00:00:00", fixed_size=False)
update_mounts()


def fetch_system_timezone():
	"""
	Retrieve the system timezone by reading from /usr/config/timezone_config.
	If the file is missing or invalid, returns 'Europe/London' as a fallback.
	"""
	timezone_file = '/usr/config/timezone_config'
	fallback_timezone = 'Europe/London'

	try:
		if exists(timezone_file):
			with open(timezone_file, 'r') as file:
				tz = file.read().strip()
				if '/' in tz and not tz.startswith('#'):
					return tz
	except (IOError, PermissionError):
		pass

	return fallback_timezone


def has_enough_free_space(path, min_bytes_required=50 * 1024 * 1024):
	"""Check if the given path has at least min_bytes_required free space."""
	statv = statvfs(path)
	free_bytes = statv.f_bavail * statv.f_frsize
	return free_bytes >= min_bytes_required


def get_cpu_count():
	"""Get number of CPU cores from /proc/cpuinfo"""
	try:
		with open("/proc/cpuinfo", "r") as f:
			cpuinfo = f.read()
		return cpuinfo.count("processor\t:")
	except:
		return 1  # Fallback to single core


def test_server_connection(portal):
	try:
		response = requests.get(portal, timeout=5)
		return f"Server status: {response.status_code}"
	except Exception as e:
		return f"Connection failed: {str(e)}"


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


class StalkerPortalConverter(Screen):
	skin = """
		<screen name="StalkerPortalConverter" position="center,center" size="1280,720" title="Stalker Portal Converter" backgroundColor="#16000000">
			<widget name="title" position="10,10" size="1260,40" font="Regular;30" halign="center" foregroundColor="#00ffff" />
			<widget name="portal_label" position="10,60" size="300,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
			<widget name="portal_input" position="320,60" size="950,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
			<widget name="mac_label" position="10,100" size="300,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
			<widget name="mac_input" position="320,100" size="950,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
			<widget name="file_label" position="10,140" size="300,30" font="Regular;24" foregroundColor="#ffffff" zPosition="2" />
			<widget name="file_input" position="320,140" size="950,30" font="Regular;24" backgroundColor="#252525" zPosition="2" />
			<widget name="portal_list_label" position="10,180" size="1260,30" font="Regular;24" foregroundColor="#ffff00" zPosition="2" scrollbarMode="showNever" />
			<widget name="file_list" position="10,220" size="1260,300" scrollbarMode="showOnDemand" itemHeight="40" font="Regular;28" backgroundColor="#252525" zPosition="2" />
			<widget name="status" position="10,565" size="1260,59" font="Regular;26" foregroundColor="#00ff00" halign="center" zPosition="2" />
			<ePixmap position="10,650" pixmap="skin_default/buttons/red.png" size="30,30" alphatest="blend" zPosition="2" />
			<widget name="key_red" font="Regular;28" position="50,650" size="200,30" halign="left" backgroundColor="black" zPosition="1" transparent="1" />
			<ePixmap position="270,650" pixmap="skin_default/buttons/green.png" size="30,30" alphatest="blend" zPosition="2" />
			<widget name="key_green" font="Regular;28" position="310,650" size="200,30" halign="left" backgroundColor="black" zPosition="2" transparent="1" />
			<ePixmap position="540,650" pixmap="skin_default/buttons/yellow.png" size="30,30" alphatest="blend" zPosition="2" />
			<widget name="key_yellow" font="Regular;28" position="580,650" size="200,30" halign="left" backgroundColor="black" zPosition="2" transparent="1" />
			<ePixmap position="810,650" pixmap="skin_default/buttons/blue.png" size="30,30" alphatest="blend" zPosition="2" />
			<widget name="key_blue" font="Regular;28" position="850,650" size="200,30" halign="left" backgroundColor="black" zPosition="2" transparent="1" />
			<eLabel name="" position="1067,645" size="52,52" backgroundColor="#00ffff" foregroundColor="#000000" halign="center" valign="center" transparent="0" cornerRadius="26" font="Regular; 17" zPosition="1" text="OK" />
			<eLabel name="" position="1130,645" size="52,52" backgroundColor="#00ffff" foregroundColor="#000000" halign="center" valign="center" transparent="0" cornerRadius="26" font="Regular; 17" zPosition="1" text="INFO" />
			<eLabel name="" position="1200,645" size="52,52" backgroundColor="#00ffff" foregroundColor="#000000" halign="center" valign="center" transparent="0" cornerRadius="26" font="Regular; 17" zPosition="1" text="EXIT" />
		</screen>"""

	def __init__(self, session):
		Screen.__init__(self, session)
		self.session = session

		self.portal_list = []
		self.playlist_file = ""
		self.channels = []

		self["title"] = Label(_("Stalker Portal to M3U Converter v.%s") % currversion)
		self["portal_label"] = Label(_("Portal URL:"))
		self["portal_input"] = Label(config.plugins.stalkerportal.portal_url.value)
		self["mac_label"] = Label(_("MAC Address:"))
		self["mac_input"] = Label(config.plugins.stalkerportal.mac_address.value)
		self["file_label"] = Label(_("Output File:"))
		self["file_input"] = Label(self.get_output_filename())
		self["portal_list_label"] = Label(_("Valid Portals from Selected File:"))
		self["file_list"] = MenuList([])
		self["status"] = Label(_("Ready - Select a file or enter URL/MAC"))
		self["key_red"] = Label(_("Clear"))
		self["key_green"] = Label("")
		self["key_yellow"] = Label(_("Select File"))
		self["key_blue"] = Label(_("Edit"))

		self["actions"] = ActionMap(
			["StalkerPortalConverter"],
			{
				"cancel": self.close,
				"exit": self.close,
				"info": self.show_info,
				"red": self.clear_fields,
				"green": self.convert,
				"yellow": self.select_output_dir,
				"blue": self.edit_settings,
				"ok": self.select_portal,
				"up": self.keyUp,
				"down": self.keyDown,
				"left": self.keyLeft,
				"right": self.keyRight,
				# "showVK": self.edit_settings,
			}, -1
		)

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
		# Ensure url ends with '/c/'
		if not url.endswith("/c/"):
			url = url.rstrip("/") + "/c/"
		pattern = compile(r'^https?://[^\s/$.?#].[^\s]*$')
		return bool(pattern.match(url)), url

	def edit_settings(self):
		menu = [
			(_("Edit Portal URL"), self.edit_portal),
			(_("Edit MAC Address"), self.edit_mac),
			(_("Change Output Directory"), self.select_output_dir),
			(_("Delete Playlist File"), self.delete_playlist),
			(_("Information"), self.show_info)
		]
		self.session.openWithCallback(self.menu_callback, MenuDialog, menu)

	def edit_portal(self):

		def portal_callback(portal):
			if portal:
				config.plugins.stalkerportal.portal_url.value = portal
				self["portal_input"].setText(portal)
				self["file_input"].setText(self.get_output_filename())

		self.session.openWithCallback(
			portal_callback,
			VirtualKeyBoard,
			title=_("Enter Portal URL (e.g. http://example.com/c/ or http://example.com:8088/c/)"),
			text=config.plugins.stalkerportal.portal_url.value
		)

	def edit_mac(self):

		def mac_callback(mac):
			if mac:
				config.plugins.stalkerportal.mac_address.value = mac
				self["mac_input"].setText(mac)
				self["file_input"].setText(self.get_output_filename())

		self.session.openWithCallback(
			mac_callback,
			VirtualKeyBoard,
			title=_("Enter MAC Address (e.g., 00:1A:79:XX:XX:XX)"),
			text=config.plugins.stalkerportal.mac_address.value
		)

	def menu_callback(self, result):
		if result:
			result[1]()

	def select_portal(self):
		"""Select a portal from the list"""
		selection = self["file_list"].getCurrent()
		if selection and self.portal_list:
			idx = self["file_list"].getSelectedIndex()
			if idx < len(self.portal_list):
				display, portal, mac = self.portal_list[idx]
				config.plugins.stalkerportal.portal_url.value = portal
				config.plugins.stalkerportal.mac_address.value = mac
				self["portal_input"].setText(portal)
				self["mac_input"].setText(mac)
				self["file_input"].setText(self.get_output_filename())
				self["status"].setText(_("Selected: ") + basename(self.playlist_file))

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

			# print("PORTAL LIST:", self.portal_list)
			# print("DISPLAY LIST:", display_list)

			if valid_count > 0:
				self["status"].setText(_("Loaded {} valid portals from {}").format(valid_count, basename(file_path)))
			else:
				self["status"].setText(_("No valid portals found in file"))

		except Exception as e:
			self["status"].setText(_("Error: ") + str(e))

	def parse_playlist_entry(self, lines, start_index):
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

	def convert(self):
		"""Convert to M3U file with actual channel list"""
		output_path = config.plugins.stalkerportal.output_dir.value
		if not has_enough_free_space(output_path, min_bytes_required=100 * 1024 * 1024):
			self["status"].setText(_("Not enough space on {}!").format(output_path))
			return
		try:
			portal = config.plugins.stalkerportal.portal_url.value.strip()
			mac = config.plugins.stalkerportal.mac_address.value.strip()
			output_file = self.get_output_filename()
			print("Portal URL:", portal)
			print("MAC address:", mac)
			print("Output file:", output_file)
		except Exception as e:
			self["status"].setText(_("Error accessing configuration values"))
			self.session.open(MessageBox, _("Failed to read portal or MAC configuration:\n") + str(e), MessageBox.TYPE_ERROR)
			return

		# Validate inputs
		if not portal or not mac:
			self["status"].setText("Error: Portal and MAC are required")
			self.session.open(MessageBox, _("Please enter portal URL and MAC address"), MessageBox.TYPE_ERROR)
			return

		if not self.validate_portal_url(portal):
			self["status"].setText(_("Error: Invalid portal URL"))
			self.session.open(MessageBox, _("Invalid portal URL format"), MessageBox.TYPE_ERROR)
			return

		if not self.validate_mac_address(mac):
			self["status"].setText(_("Error: Invalid MAC address"))
			self.session.open(MessageBox, _("Invalid MAC address format"), MessageBox.TYPE_ERROR)
			return

		# Create output directory
		output_dir = dirname(output_file)
		if not exists(output_dir):
			try:
				makedirs(output_dir)
			except Exception as e:
				error = _("Cannot create directory: ") + str(e)
				self["status"].setText(error)
				self.session.open(MessageBox, error, MessageBox.TYPE_ERROR)
				return

		# Show initial status
		self["status"].setText(_("Starting conversion process..."))

		# Run in a background thread to avoid blocking the GUI
		from threading import Thread
		self.worker_thread = Thread(target=self.convert_thread, args=(portal, mac, output_file))
		self.worker_thread.start()

	def convert_thread(self, portal, mac, output_file):
		"""Background thread for conversion process"""
		try:
			# Retrieve actual channel list
			self.update_status(_("Step 1/3: Connecting to portal..."))
			success = self.get_channel_list(portal, mac)

			if not success or not self.channels:
				self.update_status(_("Failed to retrieve channel list"))
				self.session.open(
					MessageBox,
					_("Could not retrieve channel list from portal. Check URL and MAC."),
					MessageBox.TYPE_ERROR
				)
				return

			# Create M3U content with actual channels
			self.update_status(_("Creating playlist file..."))
			try:
				with open(output_file, 'w', encoding='utf-8') as f:
					# Write M3U header
					f.write("#EXTM3U\n")
					f.write("# Portal: {}\n".format(portal))
					f.write("# MAC: {}\n".format(mac))
					f.write("# Channels: {}\n\n".format(len(self.channels)))

					# Write each channel
					for channel in self.channels:
						# EXTINF line with channel information
						f.write("#EXTINF:-1 tvg-id=\"{}\" tvg-name=\"{}\" ".format(channel['id'], channel['name']))
						f.write("tvg-logo=\"{}\" group-title=\"{}\",{}\n".format(channel['logo'], channel['group'], channel['name']))

						# Actual stream URL
						f.write("{}\n\n".format(channel['url']))

						self.update_status(_(f"Chanel Name: {channel['name']}"))

				self.update_status(_("M3U created with {} channels").format(len(self.channels)))
				self.session.open(
					MessageBox,
					_("M3U playlist successfully created with {} channels:\n{}").format(len(self.channels), output_file),
					MessageBox.TYPE_INFO
				)
			except Exception as e:
				error = _("Error creating playlist: ") + str(e)
				self.update_status(error)
				self.session.open(MessageBox, error, MessageBox.TYPE_ERROR)

		except Exception as e:
			error = _("Conversion error: ") + str(e)
			self.update_status(error)
			self.session.open(MessageBox, error, MessageBox.TYPE_ERROR)

	def update_status(self, text):
		"""Thread-safe status update"""
		from twisted.internet import reactor
		reactor.callFromThread(self._update_status_safe, text)

	def _update_status_safe(self, text):
		"""Update status in main thread"""
		self["status"].setText(text)

	def get_channel_list(self, portal, mac):
		"""Retrieve channel list with parallel processing"""
		self.channels = []
		try:
			# Create main session for initial requests
			session = requests.Session()
			retry = Retry(total=3, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
			adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
			session.mount('http://', adapter)
			session.mount('https://', adapter)

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
				self.update_status(_("Token not found!"))
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
			create_link_params = {
				"forced_storage": "0",
				"disable_ad": "0",
				"download": "0",
				"force_ch_link_check": "0",
				"JsHttpRequest": "1-xml",
				"mac": mac,
				"token": token
			}

			# Create thread-safe structures
			result_queue = queue.Queue()
			work_queue = queue.Queue()
			for channel in channels_data:
				work_queue.put(channel)

			# Worker function for parallel processing

			def channel_worker():
				"""Worker thread for processing channels"""
				thread_session = requests.Session()
				thread_adapter = HTTPAdapter(pool_connections=5, pool_maxsize=5, max_retries=Retry(total=1))
				thread_session.mount('http://', thread_adapter)
				thread_session.mount('https://', thread_adapter)

				while True:
					try:
						channel = work_queue.get_nowait()
					except queue.Empty:
						break

					for channel in channels_data:
						try:
							cmd = channel.get("cmd", "").strip()
							channel_id = channel.get("id", "")

							# Extract the actual channel name from the group-title attribute
							group_title = channel.get("group_name", "") or channel.get("category_name", "")
							channel_name = channel.get("name", "")

							# If the name contains a comma, use the part after the comma as the display name
							if ',' in channel_name:
								display_name = channel_name.split(',', 1)[1].strip()
							else:
								display_name = channel_name

							# cmd = channel.get("cmd", "").strip()
							# channel_id = channel.get("id", "")

							# Build API URL
							channel_params = {"type": "itv", "action": "create_link", "cmd": cmd}
							channel_params.update(create_link_params)
							api_url = f"{portal.rstrip('/')}/portal.php?{urlencode(channel_params)}"

							# Get stream URL
							try:
								response = thread_session.get(api_url, headers=headers, timeout=5)
								json_data = response.json()
								stream_url = json_data.get("js", {}).get("cmd", "")

								if stream_url.startswith('ffmpeg '):
									stream_url = stream_url[7:]

								if "localhost" in stream_url:
									parsed_portal = urlparse(portal)
									domain = parsed_portal.netloc
									stream_url = stream_url.replace("localhost", domain)
							except Exception:
								stream_url = api_url
							"""
							# # In channel_worker function inside get_channel_list:
							# result_queue.put({
								# "id": str(channel_id),
								# "name": unquote(str(channel.get("name", ""))),  # Decode URL encoding
								# "number": int(channel.get("number")) if isinstance(channel.get("number"), int) or (isinstance(channel.get("number"), str) and channel.get("number", "").isdigit()) else 0,
								# "group": str(channel.get("group_name", "")),
								# "logo": unquote(str(channel.get("logo", ""))),
								# "url": stream_url
							# })
							"""

							# Add result to queue
							result_queue.put({
								"id": str(channel_id),
								"name": display_name,  # Use the display name after comma
								"number": int(channel.get("number")) if channel.get("number") else 0,
								"group": group_title,  # Use group_title for category
								"logo": str(channel.get("logo", "")),
								"url": stream_url
							})

						except Exception as e:
							print(f"Worker error: {e}")
						finally:
							work_queue.task_done()

			# Start worker threads
			num_workers = min(10, total_channels)  # Max 10 workers
			workers = []
			for x in range(num_workers):
				t = threading.Thread(target=channel_worker)
				t.daemon = True
				t.start()
				workers.append(t)

			# Monitor progress
			processed = 0
			start_time = time.time()
			last_update = time.time()

			while processed < total_channels:
				try:
					# Get completed channels with timeout
					channel = result_queue.get(timeout=2.0)
					self.channels.append(channel)
					processed += 1

					# Update status every 5 seconds or 100 channels
					current_time = time.time()
					if processed % 100 == 0 or current_time - last_update > 5:
						elapsed = current_time - start_time
						speed = processed / elapsed if elapsed > 0 else 0
						self.update_status(_("Processed {}/{} channels ({:.1f}/s)").format(processed, total_channels, speed))
						last_update = current_time
				except queue.Empty:
					# Check if all workers are done
					if all(not t.is_alive() for t in workers):
						break
					continue

			work_queue.join()

			while not result_queue.empty():
				self.channels.append(result_queue.get_nowait())

			elapsed = time.time() - start_time
			speed = len(self.channels) / elapsed if elapsed > 0 else 0
			self.update_status(_("Processed {} of {} channels in {:.1f}s ({:.1f}/s)").format(
				len(self.channels), total_channels, elapsed, speed))

			return True

		except Exception as e:
			self.update_status(_("Error: ") + str(e))
			return False

	def create_fallback_channels(self, portal, mac, token):
		"""Create channels without full list when API fails"""
		self.channels = [{
			"id": "1",
			"name": "Fallback Channel",
			"number": 1,
			"group": "General",
			"logo": "",
			"url": f"{portal}/portal.php?type=itv&action=create_link&cmd=1&mac={mac}&token={token}"
		}]
		self.update_status(_("Using fallback channel mode"))
		return True

	def process_channels_safe(self, session, portal, headers, channels_data, mac, token):
		"""Safe channel processing with minimal requests"""
		self.channels = []
		total_channels = len(channels_data)
		processed = 0

		first_channel = channels_data[0] if channels_data else None
		base_url = None

		if first_channel:
			try:
				cmd = first_channel.get("cmd", "")
				channel_id = first_channel.get("id", "")
				response = session.get(
					f"{portal}/portal.php?type=itv&action=create_link&cmd={cmd}&mac={mac}&token={token}&JsHttpRequest=1-xml",
					headers=headers,
					timeout=5
				)
				json_data = response.json()
				stream_url = json_data.get("js", {}).get("cmd", "")

				if stream_url:
					# Extract base pattern
					if "://" in stream_url:
						parsed = urlparse(stream_url)
						base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
						print(f"Detected URL pattern: {base_url}")
			except Exception:
				pass

		# Process all channels
		for channel in channels_data:
			try:
				cmd = channel.get("cmd", "").strip()
				channel_id = channel.get("id", "")
				name = channel.get("name", "")

				if base_url:
					stream_url = f"{base_url}?mac={mac}&token={token}&stream={channel_id}"
				else:
					stream_url = f"{portal}/portal.php?type=itv&action=create_link&cmd={cmd}&mac={mac}&token={token}&JsHttpRequest=1-xml"

				if stream_url.startswith('ffmpeg '):
					stream_url = stream_url[7:]

				self.channels.append({
					"id": str(channel_id),
					"name": str(name),
					"number": int(channel.get("number", processed + 1)),
					"group": str(channel.get("category_name") or channel.get("group_name") or ""),
					"logo": str(channel.get("logo", "")),
					"url": stream_url
				})

				processed += 1
				if processed % 100 == 0:
					self.update_status(_("Processed {}/{} channels").format(processed, total_channels))

			except Exception as e:
				print(f"Skipping channel {channel_id}: {str(e)}")
				continue

		return True

	def select_output_dir(self):
		"""Select output directory with multiboot support"""
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
		"""Device selection management"""
		if choice and choice[1]:
			config.plugins.stalkerportal.output_dir.value = choice[1]
			self["file_input"].setText(self.get_output_filename())
			self["status"].setText(_("Selected device: ") + choice[0])
			self.browse_directory(choice[1])

	def browse_directory(self, path=None):
		"""Browse files and directories in selected directory"""
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
					# Mostra dimensione per i file
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
				title=_("Select in: {}").format(path),
				list=files
			)
		else:
			self["status"].setText(_("No files or directories found in: ") + path)

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
			# self["status"].setText(_("Loaded playlist: ") + basename(choice[1]))
			config.plugins.stalkerportal.output_dir.value = dirname(choice[1])
			self["file_input"].setText(self.get_output_filename())
			self["status"].setText(_("Loaded playlist: %s. Are you ready to convert? Press Green to proceed.") % basename(choice[1]))
			self["key_green"].setText("Convert")
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

	def keyUp(self):
		self["file_list"].up()

	def keyDown(self):
		self["file_list"].down()

	def keyLeft(self):
		self["file_list"].pageUp()

	def keyRight(self):
		self["file_list"].pageDown()

	def show_info(self):
		text = (
			"Plugin: StalkerPortalConvert to m3u\n"
			"Author: Lululla\n"
			"Version: %s\n"
			"Date: Giugno 2025\n"
			"Stalker Portal to M3U Playlist Converter\n"
			"Support: Linuxsat-support.com - corvoboys.org\n"
		) % currversion
		self.session.open(MessageBox, text, MessageBox.TYPE_INFO, timeout=10)

	def clear_fields(self):
		"""Clear input fields"""
		config.plugins.stalkerportal.portal_url.value = "http://my.server.xyz:8080/c/"
		config.plugins.stalkerportal.mac_address.value = "00:1A:79:00:00:00"
		self["portal_input"].setText(config.plugins.stalkerportal.portal_url.value)
		self["mac_input"].setText(config.plugins.stalkerportal.mac_address.value)
		self["file_input"].setText(self.get_output_filename())
		self["status"].setText(_("Fields cleared"))


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
