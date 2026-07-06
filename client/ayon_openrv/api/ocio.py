"""Helper functions to apply OCIO colorspace settings on groups.

This tries to set the relevant OCIO settings on the group's look and render
pipeline similar to what the OpenColorIO Basic Color Management package does in
OpenRV through its `ocio_source_setup` python file.

This assumes that the OpenColorIO Basic Color Management package of RV is both
installed and loaded.

"""
import os
import rv.commands
import rv.qtutils

from .lib import (
    group_member_of_type,
    active_view
)


class OCIONotActiveForGroup(RuntimeError):
    """Error raised when OCIO is not enabled on the group node."""


def get_group_ocio_look_node(group):
    """Return OCIOLook node from source group"""
    # make sure this only runs if OCIO is set
    if os.environ.get("OCIO") is None:
        return

    pipeline = group_member_of_type(group, "RVLookPipelineGroup")
    if pipeline:
        return group_member_of_type(pipeline, "OCIOLook")


def get_group_ocio_file_node(group):
    """Return OCIOFile node from source group"""
    # make sure this only runs if OCIO is set
    if os.environ.get("OCIO") is None:
        return

    pipeline = group_member_of_type(group, "RVLinearizePipelineGroup")
    if pipeline:
        return group_member_of_type(pipeline, "OCIOFile")


def set_group_ocio_colorspace(group, colorspace):
    """Set the group's OCIOFile node ocio.inColorSpace property.

    This only works if OCIO is already 'active' for the group. T

    """
    # make sure this only runs if OCIO is set
    if os.environ.get("OCIO") is None:
        return

    # RV OCIO package
    import ocio_source_setup  # noqa: F401
    node = get_group_ocio_file_node(group)

    if not node:
        raise OCIONotActiveForGroup(
            "Unable to find OCIOFile node for {}".format(group)
        )

    rv.commands.setStringProperty(
        f"{node}.ocio.inColorSpace", [colorspace], True
    )


def set_current_ocio_active_state(state):
    """Set the OCIO state for the currently active source.

    This is a hacky workaround to enable/disable the OCIO active state for
    a source since it appears to be that there's no way to explicitly trigger
    this callback from the `ocio_source_setup.OCIOSourceSetupMode` instance
    which does these changes.

    """
    # TODO: Make this logic less hacky
    # See: https://community.shotgridsoftware.com/t/how-to-enable-disable-ocio-and-set-ocio-colorspace-for-group-using-python/17178  # noqa

    group = rv.commands.viewNode()
    ocio_node = get_group_ocio_file_node(group)
    if state == bool(ocio_node):
        # Already in correct state
        return

    window = rv.qtutils.sessionWindow()
    menu_bar = window.menuBar()
    for action in menu_bar.actions():
        if action.text() != "OCIO" or action.toolTip() != "OCIO":
            continue

        ocio_menu = action.menu()

        for ocio_action in ocio_menu.actions():
            if ocio_action.toolTip() == "File Color Space":
                # The first entry is for "current source" instead
                # of all sources so we need to break the for loop
                # The first action of the file color space menu
                # is the "Active" action. So lets take that one
                active_action = ocio_action.menu().actions()[0]

                active_action.trigger()
                return

    raise RuntimeError(
        "Unable to set active state for current source. Make "
        "sure the OCIO package is installed and loaded."
    )


def set_display_ocio(display=None, view=None):
    """Activate the OCIODisplay transform on every display group.

    Direct node operations (mirrors what ocio_source_setup's "Active" +
    view menu items do) instead of walking RV's OCIO menu - the menu
    layout changes between RV versions (3.1 titles entries by device
    name), which silently broke the menu-walk approach.

    Resolution order for the view: ``AYON_RV_OCIO_VIEW`` env var, the
    ``view`` argument, then the config's default view. Invalid views fall
    back to the config default.

    Returns:
        tuple[str, str] | None: (display, view) applied, or None when
            OCIO is not active.
    """
    if os.environ.get("OCIO") is None:
        return None

    import PyOpenColorIO as OCIO

    config = OCIO.GetCurrentConfig()
    if display is None:
        display = config.getDefaultDisplay()
    view = os.environ.get("AYON_RV_OCIO_VIEW") or view
    valid_views = list(config.getViews(display))
    if view not in valid_views:
        if view:
            print(
                f"WARNING: view {view!r} not in config views {valid_views},"
                " using config default"
            )
        view = config.getDefaultView(display)

    display_groups = rv.commands.nodesOfType("RVDisplayGroup")

    # pass 1: make sure every monitor's display pipeline hosts an
    # OCIODisplay node (swaps out RV's default display color node)
    for group in display_groups:
        try:
            dpipeline = group_member_of_type(
                group, "RVDisplayPipelineGroup"
            )
            if not dpipeline:
                continue
            nodes = rv.commands.getStringProperty(
                f"{dpipeline}.pipeline.nodes"
            )
            if "OCIODisplay" not in nodes:
                rv.commands.setStringProperty(
                    f"{dpipeline}.pipeline.nodes", ["OCIODisplay"], True
                )
        except Exception as error:
            print(f"WARNING: OCIO pipeline swap failed for {group}: {error}")

    # pass 2: configure + activate each monitor's node
    for group in display_groups:
        try:
            dpipeline = group_member_of_type(
                group, "RVDisplayPipelineGroup"
            )
            docio = dpipeline and group_member_of_type(
                dpipeline, "OCIODisplay"
            )
            if not docio:
                print(f"WARNING: no OCIODisplay node in {group} - skipped")
                continue
            # disable while changing display/view (avoids shader rebuild
            # in an invalid intermediate state), same as ocio_source_setup
            rv.commands.setIntProperty(f"{docio}.ocio.active", [0], True)
            rv.commands.setStringProperty(
                f"{docio}.ocio.function", ["display"], True
            )
            rv.commands.setStringProperty(
                f"{docio}.ocio.inColorSpace", [OCIO.ROLE_SCENE_LINEAR], True
            )
            rv.commands.setStringProperty(
                f"{docio}.ocio_display.display", [display], True
            )
            rv.commands.setStringProperty(
                f"{docio}.ocio_display.view", [view], True
            )
            rv.commands.setIntProperty(f"{docio}.ocio.active", [1], True)
            device = rv.commands.getStringProperty(
                f"{group}.device.name"
            )[0]
            print(f"INFO: OCIO display on {device!r}: {display} / {view}")
        except Exception as error:
            print(f"WARNING: OCIO display setup failed for {group}: {error}")
    rv.commands.redraw()
    return display, view


def set_ocio_display_active_state():
    """Set the OCIO display active state for the currently active source.

    This is a hacky workaround to enable displays to be OCIO display
    active state.
    """

    # See: https://community.shotgridsoftware.com/t/how-to-enable-disable-ocio-and-set-ocio-colorspace-for-group-using-python/17178  # noqa
    activated_displays = []
    window = rv.qtutils.sessionWindow()
    menu_bar = window.menuBar()
    for action in menu_bar.actions():
        if action.text() != "OCIO" or action.toolTip() != "OCIO":
            continue

        ocio_menu = action.menu()

        # first collect all activated displays
        for ocio_action in ocio_menu.actions():
            display_name = ocio_action.toolTip()
            if (
                "DISPLAY" in display_name
                and ocio_action not in activated_displays
            ):
                activated_displays.append(ocio_action)

    # It could be empty if no OCIO menu is activated
    if activated_displays:
        # Set the active state for all displays
        temp_data = {
            f"displayGroup{index}_colorPipeline": ocio_action
            for index, ocio_action in enumerate(activated_displays)
        }

        for ocio_display_node, ocio_action in temp_data.items():
            node = _get_OCIODislay_nodes().get(
                ocio_display_node)
            if node is None:
                active_action = ocio_action.menu().actions()[0]
                active_action.trigger()


def _get_OCIODislay_nodes():
    nodes_by_name = {}
    # make sure we collect OCIO display nodes
    for node in rv.commands.nodes():
        group_node = rv.commands.nodeGroup(node)
        gnode_type = rv.commands.nodeType(node)
        if (
            group_node is not None
            and group_node.startswith("displayGroup")
            and group_node.endswith("_colorPipeline")
            and "OCIODisplay" in gnode_type
        ):
            nodes_by_name[group_node] = node
    return nodes_by_name

def set_group_ocio_active_state(group, state):
    """Set the OCIO state for the 'currently active source'.

    This is a hacky workaround to enable/disable the OCIO active state for
    a source since it appears to be that there's no way to explicitly trigger
    this callback from the `ocio_source_setup.OCIOSourceSetupMode` instance
    which does these changes.

    """
    # make sure this only runs if OCIO is set
    if os.environ.get("OCIO") is None:
        return

    ocio_node = get_group_ocio_file_node(group)
    if state == bool(ocio_node):
        # Already in correct state
        return

    with active_view(group):
        set_current_ocio_active_state(state)
