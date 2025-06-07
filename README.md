## 📺 Stalker Portal Playlist Converter
  
![](https://komarev.com/ghpvc/?username=Belfagor2005)

<img src="https://github.com/Belfagor2005/StalkerPortalConverter/blob/main/usr/lib/enigma2/python/Plugins/Extensions/StalkerPortalConverter/plugin.png?raw=true" width="600"/> 

<img src="[https://github.com/Belfagor2005/ScreenGrabber/blob/main/screen/screen.png](https://github.com/Belfagor2005/StalkerPortalConverter/blob/main/screen/screen.png)?raw=true" width="600"/>
---

This plugin allows you to convert a list of **Stalker Portal** URLs and MAC addresses from a `playlist.txt` file into usable M3U playlists.

### 🚀 Features

* 🧠 **Smart parsing**: Recognizes multiple formats of portal and MAC combinations
* 📁 **Custom folder selection** for output
* ⚡ **Very fast**: \~13,000 channels converted in about 1 minute
* 💻 Output is easily viewable and usable on PC or media players
* 🧾 Supports **multiple MACs per portal** and **shared MAC usage**

---
## 🎮 How to Use

| Button       | Action                                      |
|--------------|---------------------------------------------|
| 🔴 RED       | Clear Field                                 |
| 🟢 GREEN     | Convert Stalker Portal list to M3U          |
| 🟡 YELLOW    | Select Folder output and file playlist.txt  |
| 🔵 BLUE      | Edit Config                                 |
| ℹ️ Info      | Show help and usage instructions            |
---

### 📄 Input Format (`playlist.txt`)

Case-sensitive. The following formats are supported:

#### ✅ Standard Format

```
Panel: http://example.com:80/c/
MAC: 00:1A:79:XX:XX:XX
```

#### ✅ Compact Format

```
http://example.com/c/ # My Portal
00:1A:79:XX:XX:XX
```

#### ✅ Multiple MACs per Portal

```
Portal: http://server.com:8080/c
MAC1: 00:1A:79:AA:AA:AA
MAC2: 00:1A:79:BB:BB:BB
```

#### ✅ Unlabeled MAC

```
Panel http://example.com/c
00:1A:79:XX:XX:XX
```

#### ✅ Shared MAC (for multiple portals)

```
http://server1.com/c/
http://server2.com/c/ # Uses same MAC as server1
00:1A:79:XX:XX:XX
```

---

### 📂 Output

* Standard `.m3u` files for each portal
* Grouped and channelized for easy use

by Lululla
