"""Ce que la 1.5.0 livrait dans _internal et que la 1.6 ne livre plus.

La 1.6.0 a remplacé la fenêtre Qt (pywebview, PyQt6, QtWebEngine) par une
page servie au navigateur. Décompressée par-dessus la 1.5.0, comme le README
le conseille pour une mise à jour, elle laisse dans _internal près de 600 Mo
de l'ancienne version, que _installation.nettoyer_restes_de_qt retire.

Listes figées, tirées des archives publiées : les entrées de premier niveau
de _internal présentes dans la 1.5.0 et absentes de la 1.6.0. La 1.5.0 est
la seule version livrée telle quelle avec Qt, les précédentes passant par un
lanceur qui extrayait le programme ailleurs. C'est la section [InstallDelete]
d'un installeur Inno Setup, appliquée au démarrage faute d'installeur. Rien
pour macOS, où l'application se remplace d'un bloc.
"""

WINDOWS = frozenset({
    "libcrypto-3-x64.dll", "LIBPQ.dll", "libssl-3-x64.dll", "PyQt6",
    "pyqt6-6.11.0.dist-info", "pyqt6_qt6-6.11.2.dist-info",
    "pyqt6_sip-13.12.0.dist-info", "pyqt6_webengine-6.11.0.dist-info",
    "pyqt6_webengine_qt6-6.11.2.dist-info", "qtpy", "QtPy-2.4.3.dist-info",
    "webview",
})

LINUX = frozenset({
    "libasound.so.2", "libatk-1.0.so.0", "libatk-bridge-2.0.so.0",
    "libatspi.so.0", "libavahi-client.so.3", "libavahi-common.so.3",
    "libavcodec.so.61", "libavformat.so.61", "libavutil.so.59",
    "libblkid.so.1", "libbrotlicommon.so.1", "libbrotlidec.so.1",
    "libbsd.so.0", "libbz2.so.1", "libcairo-gobject.so.2", "libcairo.so.2",
    "libcap.so.2", "libcom_err.so.2", "libcups.so.2", "libdatrie.so.1",
    "libdbus-1.so.3", "libepoxy.so.0", "libfbclient.so.2",
    "libfontconfig.so.1", "libfreebl3.chk", "libfreebl3.so",
    "libfreeblpriv3.chk", "libfreeblpriv3.so", "libfreetype.so.6",
    "libfribidi.so.0", "libgbm.so.1", "libgcrypt.so.20", "libgdk-3.so.0",
    "libgdk_pixbuf-2.0.so.0", "libgio-2.0.so.0", "libglib-2.0.so.0",
    "libgmodule-2.0.so.0", "libgmp.so.10", "libgnutls.so.30",
    "libgobject-2.0.so.0", "libgpg-error.so.0", "libgraphite2.so.3",
    "libgssapi_krb5.so.2", "libgthread-2.0.so.0", "libgtk-3.so.0",
    "libharfbuzz.so.0", "libhogweed.so.6", "libicudata.so.73",
    "libicui18n.so.73", "libicuuc.so.73", "libidn2.so.0", "libjpeg.so.8",
    "libk5crypto.so.3", "libkeyutils.so.1", "libkrb5.so.3",
    "libkrb5support.so.0", "liblber.so.2", "libldap.so.2", "libltdl.so.7",
    "liblz4.so.1", "libmd.so.0", "libmount.so.1", "libmysqlclient.so.21",
    "libnettle.so.8", "libnspr4.so", "libnss3.so", "libnssckbi.so",
    "libnssdbm3.chk", "libnssdbm3.so", "libnssutil3.so", "libodbc.so.2",
    "libp11-kit.so.0", "libpango-1.0.so.0", "libpangocairo-1.0.so.0",
    "libpangoft2-1.0.so.0", "libpcre2-8.so.0", "libpixman-1.so.0",
    "libplc4.so", "libplds4.so", "libpng16.so.16", "libpq.so.5",
    "libQt6Bluetooth.so.6", "libQt6Concurrent.so.6", "libQt6Core.so.6",
    "libQt6DBus.so.6", "libQt6Designer.so.6",
    "libQt6EglFSDeviceIntegration.so.6", "libQt6FFmpegStub-crypto.so.3",
    "libQt6FFmpegStub-ssl.so.3", "libQt6FFmpegStub-va-drm.so.2",
    "libQt6FFmpegStub-va-x11.so.2", "libQt6FFmpegStub-va.so.2",
    "libQt6Gui.so.6", "libQt6Help.so.6", "libQt6Multimedia.so.6",
    "libQt6MultimediaQuick.so.6", "libQt6MultimediaWidgets.so.6",
    "libQt6Network.so.6", "libQt6Nfc.so.6", "libQt6OpenGL.so.6",
    "libQt6OpenGLWidgets.so.6", "libQt6Pdf.so.6", "libQt6PdfQuick.so.6",
    "libQt6PdfWidgets.so.6", "libQt6Positioning.so.6",
    "libQt6PositioningQuick.so.6", "libQt6PrintSupport.so.6", "libQt6Qml.so.6",
    "libQt6QmlMeta.so.6", "libQt6QmlModels.so.6", "libQt6QmlWorkerScript.so.6",
    "libQt6Quick.so.6", "libQt6Quick3D.so.6", "libQt6Quick3DAssetImport.so.6",
    "libQt6Quick3DAssetUtils.so.6", "libQt6Quick3DEffects.so.6",
    "libQt6Quick3DHelpers.so.6", "libQt6Quick3DHelpersImpl.so.6",
    "libQt6Quick3DParticles.so.6", "libQt6Quick3DPhysics.so.6",
    "libQt6Quick3DPhysicsHelpers.so.6", "libQt6Quick3DRuntimeRender.so.6",
    "libQt6Quick3DSpatialAudio.so.6", "libQt6Quick3DUtils.so.6",
    "libQt6Quick3DXr.so.6", "libQt6QuickControls2.so.6",
    "libQt6QuickControls2Basic.so.6",
    "libQt6QuickControls2BasicStyleImpl.so.6",
    "libQt6QuickControls2Fusion.so.6",
    "libQt6QuickControls2FusionStyleImpl.so.6",
    "libQt6QuickControls2Imagine.so.6",
    "libQt6QuickControls2ImagineStyleImpl.so.6",
    "libQt6QuickControls2Impl.so.6", "libQt6QuickControls2Material.so.6",
    "libQt6QuickControls2MaterialStyleImpl.so.6",
    "libQt6QuickControls2Universal.so.6",
    "libQt6QuickControls2UniversalStyleImpl.so.6", "libQt6QuickDialogs2.so.6",
    "libQt6QuickDialogs2QuickImpl.so.6", "libQt6QuickDialogs2Utils.so.6",
    "libQt6QuickEffects.so.6", "libQt6QuickLayouts.so.6",
    "libQt6QuickParticles.so.6", "libQt6QuickShapes.so.6",
    "libQt6QuickTemplates2.so.6", "libQt6QuickTest.so.6",
    "libQt6QuickTimeline.so.6", "libQt6QuickTimelineBlendTrees.so.6",
    "libQt6QuickVectorImage.so.6", "libQt6QuickVectorImageGenerator.so.6",
    "libQt6QuickWidgets.so.6", "libQt6RemoteObjects.so.6",
    "libQt6RemoteObjectsQml.so.6", "libQt6Sensors.so.6",
    "libQt6SensorsQuick.so.6", "libQt6SerialPort.so.6",
    "libQt6ShaderTools.so.6", "libQt6SpatialAudio.so.6", "libQt6Sql.so.6",
    "libQt6StateMachine.so.6", "libQt6StateMachineQml.so.6", "libQt6Svg.so.6",
    "libQt6SvgWidgets.so.6", "libQt6Test.so.6", "libQt6TextToSpeech.so.6",
    "libQt6WaylandClient.so.6", "libQt6WebChannel.so.6",
    "libQt6WebChannelQuick.so.6", "libQt6WebEngineCore.so.6",
    "libQt6WebEngineQuick.so.6", "libQt6WebEngineQuickDelegatesQml.so.6",
    "libQt6WebEngineWidgets.so.6", "libQt6WebSockets.so.6",
    "libQt6Widgets.so.6", "libQt6WlShellIntegration.so.6", "libQt6XcbQpa.so.6",
    "libQt6Xml.so.6", "libsasl2.so.2", "libselinux.so.1", "libsmime3.so",
    "libsoftokn3.chk", "libsoftokn3.so", "libswresample.so.5",
    "libswscale.so.8", "libsystemd.so.0", "libtasn1.so.6", "libthai.so.0",
    "libtommath.so.1", "libudev.so.1", "libunistring.so.5", "libX11-xcb.so.1",
    "libX11.so.6", "libXau.so.6", "libxcb-glx.so.0", "libxcb-randr.so.0",
    "libxcb-render.so.0", "libxcb-shm.so.0", "libxcb-sync.so.1",
    "libxcb-xfixes.so.0", "libXcomposite.so.1", "libXcursor.so.1",
    "libXdamage.so.1", "libXdmcp.so.6", "libXext.so.6", "libXfixes.so.3",
    "libXi.so.6", "libXinerama.so.1", "libxkbcommon.so.0", "libxkbfile.so.1",
    "libXrandr.so.2", "libXrender.so.1", "libXtst.so.6", "libzstd.so.1",
    "PyQt6", "pyqt6-6.11.0.dist-info", "pyqt6_qt6-6.11.2.dist-info",
    "pyqt6_sip-13.12.0.dist-info", "pyqt6_webengine-6.11.0.dist-info",
    "pyqt6_webengine_qt6-6.11.2.dist-info", "qtpy", "QtPy-2.4.3.dist-info",
    "webview",
})

PAR_SYSTEME = {"Windows": WINDOWS, "Linux": LINUX}
