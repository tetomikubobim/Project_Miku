Project Miku Launcher - Qt / PySide6 notice

MikuLauncher.exe is built with PyInstaller and uses Qt 6 through PySide6
(Qt for Python), which are licensed under the GNU Lesser General Public
License version 3 (LGPL-3.0). The full license texts are in this folder:

  LGPL-3.0.txt   GNU Lesser General Public License, version 3
  GPL-3.0.txt    GNU General Public License, version 3 (LGPL-3.0 is a supplement to it)

The Qt and PySide6 libraries are NOT modified. They are shipped as separate
dynamic libraries (.dll / .pyd) in the "_internal/PySide6" folder, so you can
replace them with your own build of Qt / PySide6.

Source code of Qt and PySide6:
  https://code.qt.io/
  https://pypi.org/project/PySide6/

Qt is a registered trademark of The Qt Company Ltd.
The rest of this launcher (the source code of this project) is under the MIT
license; see LICENSE.md in the project root. Other third-party notices:
THIRD_PARTY_NOTICES.md.
