# Vitis HLS driver for the GENERATED histogram kernel (gen/hist.cpp) + the
# hand-written datapath hooks, against the hand-written hist_tb.cpp.  The
# hand-written hist.cpp/run.tcl stay the diff targets; this drives the generated
# kernel through csim / csynth / cosim.
set script_dir [file dirname [file normalize [info script]]]
set data_dir [file join $script_dir "data"]

set start_at "csim"
set through "csim"
set trace_level "none"
if {[info exists ::env(PYSILICON_HIST_START_AT)]} { set start_at $::env(PYSILICON_HIST_START_AT) }
if {[info exists ::env(PYSILICON_HIST_THROUGH)]}  { set through  $::env(PYSILICON_HIST_THROUGH) }
if {[info exists ::env(PYSILICON_HIST_TRACE_LEVEL)]} { set trace_level $::env(PYSILICON_HIST_TRACE_LEVEL) }

proc stage_index {stage} {
    switch -- $stage {
        csim { return 0 }
        csynth { return 1 }
        cosim { return 2 }
        default {
            puts "PYSILICON_ERROR: Unsupported stage '$stage'."
            exit 1
        }
    }
}
if {$trace_level ni {none port all}} {
    puts "PYSILICON_ERROR: Unsupported trace level '$trace_level'."
    exit 1
}
set start_idx [stage_index $start_at]
set through_idx [stage_index $through]

if {$start_at eq "csim"} {
    open_project -reset pysilicon_hist_proj
    set_top hist
    # Generated kernel + hand-written datapath hooks (validate/compute are
    # non-templated .cpp; respond is templated and #include'd by gen/hist.hpp).
    add_files gen/hist.cpp -cflags "-I. -Igen"
    add_files hist_validate_impl.cpp -cflags "-I. -Igen"
    add_files hist_compute_impl.cpp -cflags "-I. -Igen"
    add_files -tb gen/hist_tb.cpp -cflags "-I. -Igen"
    set streamutils_cpp [file join $script_dir "include" "streamutils.cpp"]
    if {[file exists $streamutils_cpp]} {
        add_files -tb $streamutils_cpp -cflags "-I. -Igen"
    }
    open_solution -reset "solution1"
    set_part {xc7z020clg484-1}
    create_clock -period 10
} else {
    open_project pysilicon_hist_proj
    open_solution "solution1"
}

if {$start_idx <= 0 && $through_idx >= 0} {
    if {[catch {csim_design -argv "$data_dir"} res]} {
        puts "PYSILICON_ERROR: generated hist C-Simulation failed."
        puts $res
        exit 1
    }
}
if {$start_idx <= 1 && $through_idx >= 1} {
    if {[catch {csynth_design} res]} {
        puts "PYSILICON_ERROR: generated hist C-Synthesis failed."
        puts $res
        exit 1
    }
}
if {$start_idx <= 2 && $through_idx >= 2} {
    if {[catch {cosim_design -argv "$data_dir" -trace_level $trace_level} res]} {
        puts "PYSILICON_ERROR: generated hist RTL Co-Simulation failed."
        puts $res
        exit 1
    }
}

puts "PYSILICON_SUCCESS: generated hist stages $start_at through $through passed."
exit 0
