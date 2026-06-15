open_project -reset waveflow_arrayutils_slice_vitis_proj
set_top main
add_files -tb arrayutils_slice_test.cpp

set script_dir [file dirname [file normalize [info script]]]
set streamutils_cpp [file join $script_dir "streamutils.cpp"]
if {![file exists $streamutils_cpp]} {
    set streamutils_cpp [file join $script_dir "include" "streamutils.cpp"]
}
if {[file exists $streamutils_cpp]} {
    add_files -tb $streamutils_cpp
}

open_solution -reset "solution1"
set_part {xc7z020clg484-1}
create_clock -period 10
set in_full   [file join $script_dir "in_full.txt"]
set in_repl   [file join $script_dir "in_repl.txt"]
set out_read  [file join $script_dir "out_read.txt"]
set out_write [file join $script_dir "out_write.txt"]

if {[catch {csim_design -argv "$in_full $in_repl $out_read $out_write"} res]} {
    puts "WAVEFLOW_ERROR: HLS C-Simulation failed."
    puts $res
    exit 1
}
puts "WAVEFLOW_SUCCESS: Arrayutils slice Vitis conformance passed."
exit 0
