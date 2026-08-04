LAUNCHD_LABEL := mwm
LAUNCHD_PLIST := $(HOME)/Library/LaunchAgents/$(LAUNCHD_LABEL).plist
LAUNCHD_DOMAIN := gui/$(shell id -u)
LOCAL_BIN := $(HOME)/.local/bin

.PHONY: all lint fix test clean install uninstall install_bin install_plist uninstall_bin uninstall_plist
.SILENT:

all: fix lint test

lint:
	uv run ruff --quiet --config pyproject.toml check .
	uv run basedpyright --project pyproject.toml --level error .

fix:
	uv run ruff --quiet --config pyproject.toml check --extend-select I --fix-only --fix .
	uv run ruff format --quiet .

test:
	uv run python -m doctest README.md *.py

clean:
	rm -rf build dist .ruff_cache __pycache__ *.spec

install: install_bin install_plist

install_plist: install_bin
	mkdir -p $(HOME)/Library/LaunchAgents
	$(LOCAL_BIN)/mwm launchd-plist > $(LAUNCHD_PLIST)
	-launchctl bootout $(LAUNCHD_DOMAIN) $(LAUNCHD_PLIST)
	launchctl bootstrap $(LAUNCHD_DOMAIN) $(LAUNCHD_PLIST)
	launchctl kickstart -k $(LAUNCHD_DOMAIN)/$(LAUNCHD_LABEL)

install_bin:
	mkdir -p $(LOCAL_BIN)
	uv run --with pyinstaller --with-requirements mwm.py pyinstaller --noconfirm --onefile --log-level WARN --distpath $(LOCAL_BIN) mwm.py

uninstall: uninstall_plist uninstall_bin

uninstall_plist:
	-launchctl bootout $(LAUNCHD_DOMAIN) $(LAUNCHD_PLIST)
	rm -f $(LAUNCHD_PLIST)

uninstall_bin:
	rm -f $(LOCAL_BIN)/mwm
