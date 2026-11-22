# NetCraft third-party notices

NetCraft's own code is licensed under [MIT](LICENSE). The components below
keep their own licenses. The portable Windows candidate preserves the Python
runtime notices in `runtime/` and the wheel license and notice files in
`app/*-*.dist-info/`. `licenses/` adds the OpenSSL license, CPython's Expat
and Windows CRT notices, license texts for its packaged compression and
decimal inputs, the Qt GNU texts and exception, and the attributions referenced
by the retained QtBase modules. The attributions are an upstream
superset because optional Qt build features vary by platform.

| Component in the portable candidate | Version | Notices and source |
| --- | --- | --- |
| CPython embeddable distribution | 3.14.10 x64 | Python Software Foundation license and bundled-component notices in `runtime/LICENSE.txt`. [CPython release and source](https://www.python.org/downloads/release/python-31410/) |
| OpenSSL included in CPython | 3.5.9 | Apache 2.0. [OpenSSL releases and source](https://openssl-library.org/source/) |
| SQLite Windows DLL | 3.54.1 x64 | Public domain. This DLL replaces the copy shipped in the CPython archive. [SQLite source and binaries](https://www.sqlite.org/download.html) · [SQLite copyright](https://www.sqlite.org/copyright.html) |
| Qt DLLs and plugins, PySide6 Essentials, Shiboken6 | 6.12.0 | Qt Core, Gui, Widgets, OpenGL and OpenGLWidgets, with the Windows platform and modern style plugins. Their QtBase attribution records and referenced notices are under `licenses/qt/`. The PyPI community wheel metadata lists LGPL 3.0, GPL 2.0 and GPL 3.0 alternatives. The license of each component governs its use. [QtBase v6.12.0](https://github.com/qt/qtbase/tree/v6.12.0) · [PySide and Shiboken v6.12.0](https://github.com/pyside/pyside-setup/tree/v6.12.0) · [Qt for Python notices](https://doc.qt.io/qtforpython-6/licenses.html) |
| dnspython | 2.9.2 | ISC. [Release and source](https://pypi.org/project/dnspython/2.9.2/) |
| PyQtGraph | 0.14.0 | MIT. [Release and source](https://pypi.org/project/pyqtgraph/0.14.0/) |
| NumPy and bundled numerical libraries | 2.5.5, SciPy OpenBLAS 0.3.34.237.2 | BSD 3-Clause and component licenses stated in the NumPy wheel notices, including OpenBLAS and its LAPACK sources. `app/numpy-*.dist-info/` preserves the upstream notice directory and wheel metadata. [Release and source](https://pypi.org/project/numpy/2.5.5/) · [SciPy OpenBLAS](https://pypi.org/project/scipy-openblas64/0.3.34.237.2/) |
| Colorama | 0.4.6 | BSD 3-Clause. [Release and source](https://pypi.org/project/colorama/0.4.6/) |
| Microsoft Visual C++ runtime components | Exact file versions in `native-files.json` | Microsoft Distributable Code in the upstream CPython, PySide6, Shiboken6 and NumPy files. The portable ZIP preserves the upstream copies. [Microsoft redistribution terms](https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files) · [CPython Windows conditions](https://github.com/python/cpython/blob/v3.14.10/PC/crtlicense.txt) |

The bundle includes exact file paths, versions when reported by the binary,
and SHA-256 digests in `native-files.json`. `runtime-manifest.json` identifies
the imported runtime versions. The embedded Python release also contains
bzip2, Expat, libffi, libmpdec, LZMA, zlib-ng and Zstandard. The Python release
notice includes the bzip2 and libffi texts. `licenses/cpython/` adds the
mpdecimal copyright and license, XZ notices, and zlib-ng and Zstandard license
texts from the tagged CPython source dependencies. [Windows build input versions](https://github.com/python/cpython/blob/v3.14.10/PCbuild/get_externals.bat) ·
[CPython source dependencies](https://github.com/python/cpython-source-deps)

The portable layout uses separate shared libraries. A user can retain a copy
of the folder and replace the Qt DLLs and plugins with ABI-compatible builds
at the same paths to use a modified library. The application imposes no
signature check on those libraries. Keep the matching PySide and Shiboken
bindings together when replacing them.

This is Qt's community distribution. The commercial notice preserved from
the wheel is an upstream document and grants no commercial entitlement.
Before redistributing the ZIP, confirm the exact corresponding source and
build information for the delivered Qt libraries and bindings, provide it
through the approved download route, and confirm that the distribution terms
permit modification of the Qt portions and reverse engineering for debugging
those modifications. Review Microsoft Distributable Code terms independently.
The local source snapshots and upstream links support that review. A recipient
source-delivery route and approval for public binary distribution are pending.
[Qt open-source redistribution obligations](https://www.qt.io/development/open-source-lgpl-obligations)
