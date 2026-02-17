PREFIX ?= /usr

all:
	@true

install:
	install -D -m 755 update_tray.py $(DESTDIR)$(PREFIX)/lib/remote-update-tray/update_tray.py
	install -D -m 755 remote-update-tray $(DESTDIR)$(PREFIX)/bin/remote-update-tray
	install -D -m 644 remote-update-tray.desktop $(DESTDIR)/etc/xdg/autostart/remote-update-tray.desktop
