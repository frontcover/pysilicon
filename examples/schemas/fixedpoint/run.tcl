# Vitis HLS C-simulation driver for one fixed-point conformance config.
# The per-config quantize_tb.cpp + in_values.txt are written next to this script
# (each config runs in its own directory); csim writes out_bits.txt back here.
open_project -reset fixedpoint_conf_proj
set_top main
add_files -tb quantize_tb.cpp

open_solution -reset "solution1"
set_part {xc7z020clg484-1}
create_clock -period 10

set script_dir [file dirname [file normalize [info script]]]
set in_path [file join $script_dir "in_values.txt"]
set out_path [file join $script_dir "out_bits.txt"]

if {[catch {csim_design -argv "$in_path $out_path"} res]} {
    puts "PYSILICON_ERROR: HLS C-Simulation failed."
    puts $res
    exit 1
}
puts "PYSILICON_SUCCESS: fixedpoint conformance csim passed."
exit 0
