SUMMARY = "Lululla"
MAINTAINER = "Lululla"
SECTION = "base"
PRIORITY = "required"
LICENSE = "proprietary"

require conf/license/license-gplv2.inc

inherit gitpkgv

SRCREV = "${AUTOREV}"
PV = "1.0+git${SRCPV}"
PKGV = "1.0+git${GITPKGV}"
VER ="1.0"
PR = "r0"

SRC_URI = "git://github.com/Belfagor2005/StalkerPortalConverter.git;protocol=https;branch=main"

S = "${WORKDIR}/git"

FILES_${PN} = "/usr/* /etc*"

do_install() {
    cp -rp ${S}/usr* ${D}/ 
}
