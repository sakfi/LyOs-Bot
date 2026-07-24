[app]

# (str) Title of your application
title = LyosBot

# (str) Package name
package.name = lyosbot

# (str) Package domain (needed for android/ios packaging)
package.domain = org.lyosbot

# (str) Source code where the main.py live
source.dir = .

# (list) Source files to include (let empty to include all the files)
source.include_exts = py,png,jpg,kv,atlas,txt,json

# (list) List of directory to exclude (let empty to not meclude any directory)
source.exclude_dirs = tests, bin, .git, .github, __pycache__

# (string) Application versioning (method 1)
version = 1.0.0

# (list) Application requirements
# comma separated e.g. requirements = sqlite3,kivy
requirements = python3,httpx,colorama,certifi

# (str) Supported orientation (one of landscape, sensorLandscape, portrait or all)
orientation = portrait

# (bool) Indicate if the application should be fullscreen or not
fullscreen = 0

# (list) Permissions
android.permissions = INTERNET, ACCESS_NETWORK_STATE

# (int) Target Android API
android.api = 33

# (int) Minimum API your APK will support
android.minapi = 21

# (str) Android NDK version to use
android.ndk = 25b

# (list) List of Java .jar files to add to the libs so that Pygame can use it
# android.add_jars = foo.jar,bar.jar

# (list) Architecture to build for, candidates: armeabi-v7a, arm64-v8a, x86, x86_64
android.archs = arm64-v8a, armeabi-v7a

# (bool) Enable AndroidX support. Required when targeting API 29+
android.enable_androidx = True

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 2

# (int) Display warning if buildozer is run as root (0 = false, 1 = true)
warn_on_root = 1
