# NetCraft 0.5.0 Windows runtime and redistribution audit

Reviewed November 21–22, 2026. Findings refer to the Phase 5.1 portable ZIP
listed in the [release-readiness report](phase-5-release-readiness.md).

## Runtime inventory

`runtime-manifest.json` records the imported version of every packaged Python
distribution and native runtime, the NetCraft wheel digest and the build
toolchain. `native-files.json` records each of 77 EXE, DLL and PYD files by
path, SHA-256 and any file version and product metadata supplied by the
binary. `bundle-files.json` verifies every payload member. The Qt source
attribution inventory lists upstream component versions and license evidence,
including optional and other-platform components. An attribution record by
itself does not establish that the wheel enabled the corresponding feature.

| Runtime | Evidence and update disposition |
| --- | --- |
| PySide6 Essentials, Shiboken6 and Qt 6.12.0 | PyPI still returns 6.12.0 for both bindings. Retained Qt modules are Core, Gui, Widgets, OpenGL and OpenGLWidgets, with the Windows platform and modern Windows style plugins. The exact files and side-by-side Microsoft runtime copies are inventoried. Qt's schedule listed 6.12.1 for November 23, with release and matching Windows wheel availability still to be verified. [PySide6 Essentials](https://pypi.org/project/PySide6-Essentials/) · [Shiboken6](https://pypi.org/project/shiboken6/) · [Qt release plan](https://wiki.qt.io/Qt_6.12_Release) |
| CPython 3.14.10 and OpenSSL 3.5.9 | Official x64 embed input verified by the Python release digest. OpenSSL still listed 3.5.9 as the current 3.5 release. The 3.5.10 GitHub ref and the official Python 3.14.11 x64 embed URL returned 404. Keep the supported vendor runtime pair until suitable fixed inputs exist. [CPython signed release page](https://www.python.org/downloads/release/python-31410/) · [OpenSSL downloads](https://openssl-library.org/source/) |
| SQLite 3.54.1 | Official x64 DLL with the vendor SHA3-256 replaces the older copy in CPython. The bundled runtime reported 3.54.1 and passed persistence, export and integrity checks. The SQLite release and retrospective CVE list were checked. App queries bind parameters. App history access can still expose SQLite's file parser to a user-supplied database, so the documented scope uses trusted local history files. [SQLite downloads](https://www.sqlite.org/download.html) · [SQLite CVEs](https://www.sqlite.org/cves.html) |
| dnspython 2.9.2, PyQtGraph 0.14.0, NumPy 2.5.5 and Colorama 0.4.6 | These are the observed current PyPI versions. DNS wire parsing and chart rendering are in normal workflows. The eight pinned shipping and build packages queried in OSV returned zero records, including the two Qt bindings, pip 26.3 and setuptools 84.2.1. This query does not assess nested native libraries. [OSV batch API](https://google.github.io/osv.dev/post-v1-querybatch/) |
| NumPy nested runtime | The exact x64 wheel reports SciPy OpenBLAS 0.3.34.237.2. Its OpenBLAS line is newer than the range the maintainer identifies for CVE-2021-4048. Chart inputs are app-generated numerical arrays. NetCraft has no feature that loads arbitrary NumPy or pickle files. Bundled numerical notices and the renamed Microsoft runtime remain with the vendor wheel payload. [OpenBLAS release](https://github.com/OpenMathLib/OpenBLAS/releases/tag/v0.3.34) · [OpenBLAS security policy](https://github.com/OpenMathLib/OpenBLAS/security) |
| CPython nested runtime | The build records bzip2 1.0.8, libffi 3.4.4, libmpdec 4.0.0, XZ 5.2.5, zlib-ng 2.2.4 and Zstandard 1.5.7. Runtime probes confirmed Expat 2.9.0, mpdecimal 4.0.0 and Zstandard 1.5.7. CPython exposes compression and XML APIs in the runtime, while the NetCraft diagnostics do not expose archive extraction or XML decoding. Windows DNS uses `ctypes` and therefore exercises libffi. The XZ input predates the upstream 5.6.0 and 5.6.1 compromise. LibTomMath and the attach-debugger extension were omitted after inspecting PE imports and the available modules. [CPython Windows native inputs](https://github.com/python/cpython/blob/v3.14.10/PCbuild/get_externals.bat) · [Expat releases](https://github.com/libexpat/libexpat/releases) · [XZ upstream information](https://tukaani.org/xz-backdoor/) |
| Microsoft runtimes and Windows dependencies | Exact Microsoft DLL versions in the ZIP include 14.44.35211.0 from Qt bindings, 14.51.36247.0 from CPython and the renamed NumPy copy at 14.40.33810.0. Preserved upstream DLLs have separate terms and servicing histories. The launcher targets .NET Framework 4.8 and uses Windows-provided CLR and system DLLs. Normal and delay PE import inspection found each named dependency in the ZIP, host System32 or the Windows API-set namespace. This does not establish which DLL a clean machine will select or whether every deployed CRT copy is eligible under the distributor's terms. [Microsoft Visual C++ redistributables](https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist) · [Microsoft redistribution terms](https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files) |

The application imports PySide6 Core, Gui and Widgets. PyQtGraph also needs the
OpenGL imports, even though the NetCraft charts use their standard raster
configuration. The Windows platform and style plugins' PE dependencies point
to retained Qt libraries. Scanning all 77 shipped PE files found zero normal
or delay imports of Qt Network, QML, Quick or Qt SQL. No corresponding Qt
modules, Python bindings or plugins appear in the ZIP. The app retains Python
sockets, dnspython, Windows DNS Client, Python TLS and SQLite. Loader
resolution and workflows were also exercised from the extracted archive.

## Advisory applicability and reachability

| Finding | Applicability and evidence |
| --- | --- |
| Qt Network CVE-2026-116337 | The original 6.12.0 bundle contained the affected module. The rebuilt ZIP excludes Qt Network and its TLS and network plugins. There were no retained PE dependencies on them. NetCraft HTTP uses Python sockets and its own response parser, and its HTTPS path uses Python `ssl`. The issue is excluded from this ZIP by component removal. [Qt advisory](https://www.qt.io/blog/security-issue-cve-2026-116337) |
| Qt Network CVE-2026-105943 | Qt publishes a 6.12 patch for the Windows SSPI/NTLM path in `QAuthenticator`. The downloaded patch matched the vendor checksum. The affected Qt Network module and its backends are omitted from this ZIP. NetCraft's HTTP diagnostics have no Qt auth code. [Qt patch and digest](https://download.qt.io/official_releases/qt/6.12/CVE-2026-105943-qtbase-6.12.diff.mirrorlist) |
| Qt Core CVE-2026-105924 | Qt Core 6.12.0 stays in the bundle. The input path in the advisory decodes CBOR and converts it to Qt JSON. NetCraft has no direct CBOR or Qt JSON calls, and exports use Python's JSON encoder. Independent or indirect library paths remain unverified. Keep this finding open until fixed Qt inputs are installed and retested. [Qt advisory](https://www.qt.io/blog/security-advisory-cve-2026-105924) |
| Other Qt module advisories | Qt WebSockets, HttpServer, 3D, Bluetooth and Qt5Compat are outside the rebuilt payload. Their reviewed 6.12 advisory families include CVE-2026-114369, 114366, 110944, 111487, 120973, 105925 and 100140. Neither removed optional decoders nor Qt's unrelated protocol modules are needed by the supported NetCraft features. [Qt's vulnerability index](https://wiki.qt.io/List_of_known_vulnerabilities_in_Qt_products) · [6.12 patch index](https://download.qt.io/official_releases/qt/6.12/) |
| OpenSSL CVE-2026-107493 | The affected 3.5.9 library is included. NetCraft can load a local CA file through Python's `SSLContext.load_verify_locations`. CPython delegates that file path to the OpenSSL verification API. The reviewed OpenSSL PEM trust-file loader passes a null stack to `PEM_X509_INFO_read_bio_ex`, while the advisory requires an existing caller stack. CPython's inline certificate loader uses individual X.509 decoders. These entry paths do not meet the advisory precondition. [CPython SSL implementation](https://github.com/python/cpython/blob/v3.14.10/Modules/_ssl.c) · [OpenSSL trust-file loader](https://github.com/openssl/openssl/blob/openssl-3.5.9/crypto/x509/by_file.c) |
| OpenSSL CVE-2026-35190 | Requires a TLS server configured with the Suite B certificate flag. The verified app creates a TLS client context for supplied HTTPS targets. Test TLS servers are isolated fixtures. The app has no TLS server or Suite B control. |
| OpenSSL CVE-2026-124709, 88822, 95691 | Applicable to the shipped OpenSSL version under QUIC-specific conditions. Product networking uses TCP and normal UDP datagrams. Neither the app nor its CPython TLS wrapper exposes a QUIC workflow. |
| OpenSSL CVE-2026-88823, 95692 and 107496 | The advisory paths require direct AES-GCM-SIV or CMS use. The app uses standard TLS sockets and no AES-GCM-SIV or CMS interface. OpenSSL 3.5.10 is the upstream fix level for the eight entries reviewed above. [OpenSSL 3.5 advisory list](https://openssl-library.org/news/vulnerabilities-3.5/index.html) |
| CPython November advisories | Check the 3.14.10 FIPS hashing regression CVE-2026-119170, cookie attribute injection CVE-2026-16052, IMAP STARTTLS CVE-2026-115559 and stdlib CLI shadowing CVE-2026-115791 when refreshing the bundled runtime. NetCraft has no FIPS workflow, cookie emission API, IMAP protocol or archive CLI flow. The embedded runtime supports general Python execution, so this observation applies to NetCraft's entry points. [CPython 3.14 change log](https://docs.python.org/3.14/whatsnew/changelog.html) |

The development environment's pre-existing pip 25.0.1 returned 12 OSV
records, including aliases. Python also identifies pip releases older than
26.2.2 as affected by CVE-2026-133949. The candidate build requires pip
26.3. Fresh installations used hash-verified/offline inputs and pip 26.3.
The portable bundle contains no pip or build environment.
[pip advisory CVE-2026-133949](https://www.cve.org/CVERecord?id=CVE-2026-133949)

The QtBase inventory includes FreeType 2.14.3, HarfBuzz 14.5.0, PCRE2 10.48,
zlib 1.3.2, TinyCBOR 7.0 and the remaining platform, Unicode, rendering and
utility records for the retained modules. Each component's license, source
and version when supplied are in the JSON inventory. Chart drawing, system
fonts and dialogs can use QtGui internals. There is no app file-loading
feature for arbitrary fonts or images. The wheel's complete installed
feature and linkage manifest was not available, and this review does not
give a negative vulnerability finding for every statically linked component.
Compare that manifest or a verifiable upstream SBOM for the exact replacement
wheel before public distribution. [Qt's third-party code list](https://doc.qt.io/qt-6.12/licenses-used-in-qt.html) ·
[Qt SBOM information](https://doc.qt.io/qt-6/sbom.html)

## Redistribution evidence and open duties

- The PyPI artifacts are Qt for Python Community Edition. The matching
  metadata and license files stay in the ZIP. A retained commercial notice
  is not evidence of a commercial license. Use commercial packages only
  under independently verified terms and an applicable license.
  [Qt for Python commercial distribution](https://doc.qt.io/qtforpython-6/commercial/index.html)
- The launcher keeps libraries as separately replaceable DLLs, PYDs and
  Qt plugins in `app/PySide6` and `app/shiboken6`. Recipients can extract to
  a writable directory, close NetCraft, and replace the files with ABI-compatible
  versions at those paths. There is no package signature check or startup
  digest check that forbids those substitutions. Review that final terms
  allow modification of the library portions and reverse engineering for
  debugging those modifications. Preserve required copyright notices and
  LGPL/GPL terms, including the notice and license treatment in section 4.
  [Qt LGPL guidance](https://www.qt.io/development/open-source-lgpl-obligations) · [LGPL 3.0](https://www.gnu.org/licenses/lgpl-3.0.en.html)
- The build pins and stages the tagged QtBase and PySide/Shiboken source
  snapshots for review. The upstream tag commits are
  `025bdad181de241e81bf853c8a2d7bf3d19261a9` for QtBase and
  `f50facb83fc3dc5f3240c854668c7a6dd93871b2` for PySide/Shiboken.
  The ZIP carries GNU texts, the Qt exception and
  PySide source notices, 50 QtBase attribution records plus their referenced
  notices, the upstream wheel notices, OpenSSL's license, CPython's Expat
  and CRT notices, version-matched mpdecimal, XZ, zlib-ng and Zstandard
  notices, and the project's MIT license. The start guide identifies the
  Qt Community libraries and GNU license text locations. Corresponding source means
  the source and needed build information for the exact delivered libraries.
  Match the PyPI binaries to their exact source and build configuration,
  including vendor modifications and relevant third-party build inputs,
  before offering them publicly. Select an approved recipient source-delivery
  method, verify its URLs and preserve source availability for the applicable
  period. Local cache files and upstream tag URLs are supporting evidence for
  that work. [GPL 3.0 source conveyance provisions](https://www.gnu.org/licenses/gpl-3.0.en.html)
- Confirm Microsoft redistribution rights independently for the exact
  app-local files and the CRT code linked inside vendor binaries. CPython's
  Windows CRT conditions are included verbatim. The upstream wheel notices
  and the full per-file inventory support, but do not replace, that review.
  [Microsoft redistribution terms](https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files)

The standalone source release does not redistribute these third-party runtime
binaries. Source users install dependencies from their upstream channels. The
README and packaging instructions identify the Windows ZIP as a local
candidate with separate publication gates.
