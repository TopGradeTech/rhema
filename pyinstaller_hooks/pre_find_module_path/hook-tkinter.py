def pre_find_module_path(hook_api):
    # We package Tcl/Tk data explicitly in main.spec.
    # Keep tkinter discoverable even if environment checks fail.
    pass