# Foris - web administration interface for OpenWrt based on NETCONF
# Copyright (C) 2017 CZ.NIC, z.s.p.o. <http://www.nic.cz>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import bottle  # TODO rework this dep

from foris import fapi
from foris.form import Checkbox, MultiCheckbox

from foris.config.request_decorator import require_contract_valid  # TODO rework this dep

from foris.nuci import client, filters
from foris.nuci.modules.uci_raw import (
    Uci, Config, Section, Option, List, Value, parse_uci_bool, build_option_uci_tree,
)
from foris.utils.translators import gettext_dummy as gettext, _
from foris.form import Email

from .base import BaseConfigHandler, logger


class UcollectHandler(BaseConfigHandler):
    userfriendly_title = gettext("uCollect")

    def get_form(self):
        ucollect_form = fapi.ForisForm("ucollect", self.data,
                                       filter=filters.create_config_filter("ucollect"))
        fakes = ucollect_form.add_section(
            name="fakes",
            title=_("Emulated services"),
            description=_("One of uCollect's features is emulation of some commonly abused "
                          "services. If this function is enabled, uCollect is listening for "
                          "incoming connection attempts to these services. Enabling of the "
                          "emulated services has no effect if another service is already "
                          "listening on its default port (port numbers are listed below).")
        )

        SERVICES_OPTIONS = (
            ("23tcp", _("Telnet (23/TCP)")),
            ("2323tcp", _("Telnet - alternative port (2323/TCP)")),
            ("80tcp", _("HTTP (80/TCP)")),
            ("3128tcp", _("Squid HTTP proxy (3128/TCP)")),
            ("8123tcp", _("Polipo HTTP proxy (8123/TCP)")),
            ("8080tcp", _("HTTP proxy (8080/TCP)")),
        )

        def get_enabled_services(disabled_list):
            disabled_services = map(lambda value: value.content, disabled_list.children)
            res = [x[0] for x in SERVICES_OPTIONS if x[0] not in disabled_services]
            return res

        fakes.add_field(
            MultiCheckbox,
            name="services",
            label=_("Emulated services"),
            args=SERVICES_OPTIONS,
            multifield=True,
            nuci_path="uci.ucollect.fakes.disable",
            nuci_preproc=get_enabled_services,
            default=[x[0] for x in SERVICES_OPTIONS]
        )

        fakes.add_field(
            Checkbox,
            name="log_credentials",
            label=_("Collect credentials"),
            hint=_("If this option is enabled, user names and passwords are collected "
                   "and sent to server in addition to the IP address of the client."),
            nuci_path="uci.ucollect.fakes.log_credentials",
            nuci_preproc=parse_uci_bool
        )

        def ucollect_form_cb(data):
            uci = Uci()
            ucollect = Config("ucollect")
            uci.add(ucollect)

            fakes = Section("fakes", "fakes")
            ucollect.add(fakes)

            disable = List("disable")

            disabled_services = [x[0] for x in SERVICES_OPTIONS
                                 if x[0] not in data['services']]
            for i, service in enumerate(disabled_services):
                disable.add(Value(i, service))

            if len(disabled_services):
                fakes.add_replace(disable)
            else:
                fakes.add_removal(disable)

            fakes.add(Option("log_credentials", data['log_credentials']))

            return "edit_config", uci

        ucollect_form.add_callback(ucollect_form_cb)

        return ucollect_form


class CollectionToggleHandler(BaseConfigHandler):
    userfriendly_title = gettext("Data collection")

    def get_form(self):
        form = fapi.ForisForm("enable_collection", self.data,
                              filter=filters.create_config_filter("foris", "updater"))

        section = form.add_section(
            name="collection_toggle", title=_(self.userfriendly_title),
        )
        section.add_field(Checkbox, name="enable", label=_("Enable data collection"),
                          nuci_path="uci.foris.eula.agreed_collect",
                          nuci_preproc=lambda val: bool(int(val.value)))

        def form_cb(data):
            uci = build_option_uci_tree("foris.eula.agreed_collect", "config",
                                        data.get("enable"))
            return "edit_config", uci

        def adjust_lists_cb(data):
            uci = Uci()
            # All enabled lists
            enabled_lists = map(lambda x: x.content,
                                form.nuci_config.find_child("uci.updater.pkglists.lists").children)
            # Lists that do not need agreement
            enabled_no_agree = filter(lambda x: not x.startswith("i_agree_"), enabled_lists)
            # Lists that need agreement
            enabled_i_agree = filter(lambda x: x.startswith("i_agree_"), enabled_lists)

            # Always install lists that do not need agreement - create a copy of the list
            installed_lists = enabled_no_agree[:]
            logger.warning("no agree: %s", enabled_no_agree)
            logger.warning("installed: %s", installed_lists)
            if data.get("enable", False):
                # Include i_agree lists if user agreed with EULA
                installed_lists.extend(enabled_i_agree)
                # Add main data collection list if it's not present
                logger.warning(installed_lists)
                logger.warning("i_agree_datacollect" not in installed_lists)
                logger.warning("i_agree_datacollect" in installed_lists)
                if "i_agree_datacollect" not in installed_lists:
                    logger.warning("appending")
                    installed_lists.append("i_agree_datacollect")
            logger.warning("saving %s", installed_lists)
            # Reconstruct list of package lists
            updater = uci.add(Config("updater"))
            pkglists = updater.add(Section("pkglists", "pkglists"))
            lists = List("lists")
            for i, name in enumerate(installed_lists):
                lists.add(Value(i, name))

            # If there's anything to add, replace the list, otherwise remove it completely
            if len(installed_lists) > 0:
                pkglists.add_replace(lists)
            else:
                pkglists.add_removal(lists)

            return "edit_config", uci

        def run_updater_cb(data):
            logger.info("Checking for updates.")
            client.check_updates()
            return "none", None

        form.add_callback(form_cb)
        form.add_callback(adjust_lists_cb)
        form.add_callback(run_updater_cb)

        return form


class RegistrationCheckHandler(BaseConfigHandler):
    """
    Handler for checking of the registration status and assignment to a queried email address.
    """

    userfriendly_title = gettext("Data collection")

    @require_contract_valid(False)
    def get_form(self):
        form = fapi.ForisForm(
            "registration_check", self.data, filter=filters.create_config_filter("foris")
        )
        main_section = form.add_section(name="check_email", title=_(self.userfriendly_title))
        main_section.add_field(
            Email, name="email", label=_("Email")
        )

        def form_cb(data):
            result = client.get_registration_status(data.get("email"),
                                                    bottle.request.app.lang)
            return "save_result", {
                'success': result[0],
                'response': result[1],
            }

        form.add_callback(form_cb)
        return form


