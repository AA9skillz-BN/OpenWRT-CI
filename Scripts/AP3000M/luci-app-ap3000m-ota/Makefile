# SPDX-License-Identifier: MIT
# luci-app-ap3000m-ota —— AirPi AP3000M 一键 OTA 的 LuCI 前端
#
# 刻意不依赖 luci.mk（与 luci-app-fm350 同样的自包含做法），只需要 luci-base +
# rpcd-mod-ucode；真正的动作全部交给 /usr/sbin/ap3000m-ota，前端只负责展示与确认。

include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-ap3000m-ota
PKG_VERSION:=1.0.0
PKG_RELEASE:=1

PKG_LICENSE:=MIT
PKG_MAINTAINER:=AA9skillz-BN

include $(INCLUDE_DIR)/package.mk

define Package/$(PKG_NAME)
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=AirPi AP3000M OTA 升级
  DEPENDS:=+luci-base +rpcd-mod-ucode +jq +curl
  PKGARCH:=all
endef

define Package/$(PKG_NAME)/description
  AP3000M 一键 OTA 的图形界面：检查新版本、下载校验、保留/不保留配置升级。
  后端为 /usr/sbin/ap3000m-ota（命令行同样可用）。
endef

# 纯数据包（只有 JS/JSON/ucode），必须显式清空编译步骤，
# 否则 package.mk 会去空的 PKG_BUILD_DIR 里找 Makefile：
#   make[4]: *** No targets specified and no makefile found
define Build/Compile
endef

define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
endef

define Package/$(PKG_NAME)/conffiles
endef

define Package/$(PKG_NAME)/install
	$(INSTALL_DIR) $(1)/www/luci-static/resources/view/ap3000m-ota
	$(INSTALL_DATA) ./htdocs/luci-static/resources/view/ap3000m-ota/overview.js \
		$(1)/www/luci-static/resources/view/ap3000m-ota/overview.js

	$(INSTALL_DIR) $(1)/usr/share/luci/menu.d
	$(INSTALL_DATA) ./root/usr/share/luci/menu.d/luci-app-ap3000m-ota.json \
		$(1)/usr/share/luci/menu.d/luci-app-ap3000m-ota.json

	$(INSTALL_DIR) $(1)/usr/share/rpcd/acl.d
	$(INSTALL_DATA) ./root/usr/share/rpcd/acl.d/luci-app-ap3000m-ota.json \
		$(1)/usr/share/rpcd/acl.d/luci-app-ap3000m-ota.json

	$(INSTALL_DIR) $(1)/usr/share/rpcd/ucode
	$(INSTALL_DATA) ./root/usr/share/rpcd/ucode/ap3000m-ota.uc \
		$(1)/usr/share/rpcd/ucode/ap3000m-ota.uc
endef

define Package/$(PKG_NAME)/postinst
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] && exit 0
rm -f /tmp/luci-indexcache* 2>/dev/null
rm -rf /tmp/luci-modulecache/* 2>/dev/null
/etc/init.d/rpcd reload >/dev/null 2>&1
exit 0
endef

$(eval $(call BuildPackage,$(PKG_NAME)))
