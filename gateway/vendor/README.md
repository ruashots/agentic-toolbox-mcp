# gateway/vendor

Optional. The gateway build downloads the Camoufox browser from its official
release and checks its sha256. If you put the same zip here first, the build
uses it instead of downloading it:

    camoufox-156.0.1-beta.33-lin.x86_64.zip
    sha256 730d731153b2a16cac238a0b4a7f849c04d1fd6181c09ec6af29695af0407af8

Use this if the release download ever disappears, or to avoid downloading it
again. The rest of the build still downloads its packages from the internet.
The build refuses any other file. Zips in this folder are ignored by git.
