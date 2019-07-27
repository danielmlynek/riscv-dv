"""
Copyright 2019 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

     http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

Regression script for RISC-V random instruction generator
"""

import argparse
import os
import subprocess
import random
import re
import sys

from scripts.testlist_parser import *


def get_env_var(var):
  """Get the value of environment variable

  Args:
    var : Name of the environment variable

  Returns:
    val : Value of the environment variable
  """
  try:
    val = os.environ[var]
  except KeyError:
    print ("Please set the environment variable %0s" % var)
    sys.exit(1)
  return val


def get_generator_cmd(simulator, simulator_yaml):
  """ Setup the compile and simulation command for the generator

  Args:
    simulator      : RTL simulator used to run instruction generator
    simulator_yaml : RTL simulator configuration file in YAML format

  Returns:
    compile_cmd    : RTL simulator command to compile the instruction generator
    sim_cmd        : RTL simulator command to run the instruction generator
  """
  print("Processing simulator setup file : %s" % simulator_yaml)
  yaml_data = read_yaml(simulator_yaml)
  # Search for matched simulator
  for entry in yaml_data:
    if entry['tool'] == simulator:
      print ("Found matching simulator: %s" % entry['tool'])
      compile_cmd = entry['compile_cmd']
      sim_cmd = entry['sim_cmd']
      return compile_cmd, sim_cmd
  print ("Cannot find RTL simulator %0s" % simulator)
  sys.exit(1)


def parse_iss_yaml(iss, iss_yaml, isa):
  """Parse ISS YAML to get the simulation command

  Args:
    iss        : target ISS used to look up in ISS YAML
    iss_yaml   : ISS configuration file in YAML format
    isa        : ISA variant passed to the ISS

  Returns:
    cmd        : ISS run command
  """
  print("Processing ISS setup file : %s" % iss_yaml)
  yaml_data = read_yaml(iss_yaml)
  # Search for matched ISS
  for entry in yaml_data:
    if entry['iss'] == iss:
      print ("Found matching ISS: %s" % entry['iss'])
      cmd = entry['cmd'].rstrip()
      cmd = re.sub("\<path_var\>", get_env_var(entry['path_var']), cmd)
      if iss == "ovpsim":
        cmd = re.sub("\<variant\>", isa.upper(), cmd)
      else:
        cmd = re.sub("\<variant\>", isa, cmd)
      return cmd
  print ("Cannot find ISS %0s" % iss)
  sys.exit(1)


def get_iss_cmd(base_cmd, elf, log):
  """Get the ISS simulation command

  Args:
    base_cmd : Original command template
    elf      : ELF file to run ISS simualtion
    log      : ISS simulation log name

  Returns:
    cmd      : Command for ISS simulation
  """
  cmd = re.sub("\<elf\>", elf, base_cmd)
  cmd += (" &> %s" % log)
  return cmd


def run_cmd(cmd):
  """Run a command and return output

  Args:
    cmd : shell command to run

  Returns:
    command output
  """
  try:
    ps = subprocess.Popen(cmd,
                          shell=True,
                          universal_newlines=True,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)
  except subprocess.CalledProcessError as exc:
    print(ps.communicate()[0])
    sys.exit(1)
  return ps.communicate()[0]


def get_seed(seed):
  """Get the seed to run the generator

  Args:
    seed : input seed

  Returns:
    seed to run instruction generator
  """
  if seed >= 0:
    return seed
  else:
    return random.getrandbits(32)


def gen(test_list, simulator, simulator_yaml, output_dir, sim_only,
        compile_only, lsf_cmd, seed, cwd, verbose):
  """Run the instruction generator

  Args:
    test_list      : List of assembly programs to be compiled
    simulator      : RTL simulator used to run instruction generator
    simulator_yaml : RTL simulator configuration file in YAML format
    output_dir     : Output directory of the ELF files
    sim_only       : Simulation only
    compile_only   : Compile the generator only
    lsf_cmd        : LSF command used to run the instruction generator
    seed           : Seed to the instruction generator
    verbose        : Verbose logging
  """
  # Setup the compile and simulation command for the generator
  compile_cmd = []
  sim_cmd = ""
  compile_cmd, sim_cmd = get_generator_cmd(simulator, simulator_yaml);
  # Compile the instruction generator
  if not sim_only:
    print ("Building RISC-V instruction generator")
    for cmd in compile_cmd:
      cmd = re.sub("<out>", output_dir, cmd)
      cmd = re.sub("<cwd>", cwd, cmd)
      if verbose:
        print("Compile command: %s" % cmd)
      output = run_cmd(cmd)
      if verbose:
        print(output)
  # Run the instruction generator
  if not compile_only:
    sim_cmd = re.sub("<out>", output_dir, sim_cmd)
    sim_cmd = re.sub("<cwd>", cwd, sim_cmd)
    print ("Running RISC-V instruction generator")
    for test in test_list:
      if test['iterations'] > 0:
        rand_seed = get_seed(seed)
        cmd = lsf_cmd + " " + sim_cmd.rstrip() + \
              (" +UVM_TESTNAME=%s" % test['uvm_test']) + \
              (" +num_of_tests=%d" % test['iterations']) + \
              (" +asm_file_name=%s/asm_tests/%s" % (output_dir, test['test'])) + \
              (" +ntb_random_seed=%d" % rand_seed) + \
              (" -l %s/sim_%s.log" % (output_dir, test['test']))
        print("Run %0s to generate %d assembly tests" %
              (test['uvm_test'], test['iterations']))
        if verbose:
          print(cmd)
        try:
          output = subprocess.check_output(cmd.split(),
                                           timeout=300,
                                           universal_newlines=True)
        except subprocess.CalledProcessError as exc:
          print(output)
          sys.exit(1)
        if verbose:
          print(output)


def gcc_compile(test_list, output_dir, isa, mabi, verbose):
  """Use riscv gcc toolchain to compile the assembly program

  Args:
    test_list  : List of assembly programs to be compiled
    output_dir : Output directory of the ELF files
    isa        : ISA variant passed to GCC
    mabi       : MABI variant passed to GCC
    verbose    : Verbose logging
  """
  for test in test_list:
    for i in range(0, test['iterations']):
      prefix = ("%s/asm_tests/%s.%d" % (output_dir, test['test'], i))
      asm = prefix + ".S"
      elf = prefix + ".o"
      binary = prefix + ".bin"
      # gcc comilation
      cmd = ("%s -march=%s -mabi=%s -static -mcmodel=medany \
             -fvisibility=hidden -nostdlib \
             -nostartfiles \
             -Tscripts/link.ld %s -o %s" % \
             (get_env_var("RISCV_GCC") ,isa, mabi, asm, elf))
      print("Compiling %s" % asm)
      if verbose:
        print(cmd)
      output = subprocess.check_output(cmd.split())
      if verbose:
        print(output)
      # Convert the ELF to plain binary, used in RTL sim
      print ("Converting to %s" % binary)
      cmd = ("%s -O binary %s %s" % (get_env_var("RISCV_OBJCOPY"), elf, binary))
      output = subprocess.check_output(cmd.split())
      if verbose:
        print(output)


def iss_sim(test_list, output_dir, iss_list, iss_yaml, isa, verbose):
  """Run ISS simulation with the generated test program

  Args:
    test_list  : List of assembly programs to be compiled
    output_dir : Output directory of the ELF files
    iss_list   : List of instruction set simulators
    iss_yaml   : ISS configuration file in YAML format
    isa        : ISA variant passed to the ISS
    verbose    : Verbose logging
  """
  for iss in iss_list.split(","):
    log_dir = ("%s/%s_sim" % (output_dir, iss))
    base_cmd = parse_iss_yaml(iss, iss_yaml, isa)
    print ("%s sim log dir: %s" % (iss, log_dir))
    subprocess.run(["mkdir", "-p", log_dir])
    for test in test_list:
      for i in range(0, test['iterations']):
        prefix = ("%s/asm_tests/%s.%d" % (output_dir, test['test'], i))
        elf = prefix + ".o"
        log = ("%s/%s.%d.log" % (log_dir, test['test'], i))
        cmd = get_iss_cmd(base_cmd, elf, log)
        print ("Running ISS simulation: %s" % elf)
        run_cmd(cmd)
        if verbose:
          print (cmd)


def iss_cmp(test_list, iss, output_dir, verbose):
  """Compare ISS simulation reult

  Args:
    test_list      : List of assembly programs to be compiled
    iss            : List of instruction set simulators
    output_dir     : Output directory of the ELF files
    verbose        : Verbose logging
  """
  iss_list = iss.split(",")
  if len(iss_list) != 2:
    return
  report = ("%s/iss_regr.log" % output_dir).rstrip()
  run_cmd("rm -rf %s" % report)
  for test in test_list:
    for i in range(0, test['iterations']):
      if iss_list[1] == "spike":
        iss_list[0], iss_list[1] = iss_list[1], iss_list[0]
      elf = ("%s/asm_tests/%s.%d.o" % (output_dir, test['test'], i))
      print("Comparing ISS sim result %s/%s : %s" %
            (iss_list[0], iss_list[1], elf))
      run_cmd(("echo 'Test binary: %s' >> %s" % (elf, report)))
      iss_0_log = ("%s/%s_sim/%s.%d.log" % (output_dir, iss_list[0], test['test'], i))
      iss_1_log = ("%s/%s_sim/%s.%d.log" % (output_dir, iss_list[1], test['test'], i))
      cmd = ("./iss_cmp %s %s %s" % (iss_0_log, iss_1_log, report))
      cmd += (" &>> %s" % report)
      run_cmd(cmd)
      if verbose:
        print(cmd)
  passed_cnt = run_cmd("grep PASSED %s | wc -l" % report).strip()
  failed_cnt = run_cmd("grep FAILED %s | wc -l" % report).strip()
  summary = ("%s PASSED, %s FAILED" % (passed_cnt, failed_cnt))
  print(summary)
  run_cmd(("echo %s >> %s" % (summary, report)))
  print("ISS regression report is saved to %s" % report)


# Parse input arguments
parser = argparse.ArgumentParser()

parser.add_argument("--o", type=str, default="./out",
                    help="Output directory name")
parser.add_argument("--testlist", type=str, default="",
                    help="Regression testlist")
parser.add_argument("--isa", type=str, default="rv64imc",
                    help="RISC-V ISA subset")
parser.add_argument("--mabi", type=str, default="lp64",
                    help="mabi used for compilation, lp32 or lp64")
parser.add_argument("--test", type=str, default="all",
                    help="Test name, 'all' means all tests in the list")
parser.add_argument("--seed", type=int, default=-1,
                    help="Randomization seed, default -1 means random seed")
parser.add_argument("--iterations", type=int, default=0,
                    help="Override the iteration count in the test list")
parser.add_argument("--simulator", type=str, default="vcs",
                    help="Simulator used to run the generator, default VCS")
parser.add_argument("--simulator_yaml", type=str, default="",
                    help="RTL simulator setting YAML")
parser.add_argument("--iss", type=str, default="spike",
                    help="RISC-V instruction set simulator: spike, ovpsim")
parser.add_argument("--iss_yaml", type=str, default="",
                    help="ISS setting YAML")
parser.add_argument("--verbose", type=int, default=0,
                    help="Verbose logging")
parser.add_argument("--co", type=int, default=0,
                    help="Compile the generator only")
parser.add_argument("--so", type=int, default=0,
                    help="Simulate the generator only")
parser.add_argument("--steps", type=str, default="all",
                    help="Run steps: gen,gcc_compile,iss_sim,iss_cmp")
parser.add_argument("--lsf_cmd", type=str, default="",
                    help="LSF command. Run in local sequentially if lsf \
                          command is not specified")

args = parser.parse_args()
cwd = os.path.dirname(os.path.realpath(__file__))

if not args.iss_yaml:
  args.iss_yaml = cwd + "/yaml/iss.yaml"

if not args.simulator_yaml:
  args.simulator_yaml = cwd + "/yaml/simulator.yaml"

if not args.testlist:
  args.testlist = cwd + "/yaml/testlist.yaml"

# Create output directory
subprocess.run(["mkdir", "-p", args.o])
subprocess.run(["mkdir", "-p", ("%s/asm_tests" % args.o)])

# Process regression test list
matched_list = []
process_regression_list(args.testlist, args.test, args.iterations, matched_list)
if len(matched_list) == 0:
  sys.exit("Cannot find %s in %s" % (args.test, args.testlist))

# Run instruction generator
if args.steps == "all" or re.match("gen", args.steps):
  gen(matched_list, args.simulator, args.simulator_yaml, args.o,
      args.so, args.co, args.lsf_cmd, args.seed, cwd, args.verbose)

# Compile the assembly program to ELF, convert to plain binary
if args.steps == "all" or re.match("gcc_compile", args.steps):
  gcc_compile(matched_list, args.o, args.isa, args.mabi, args.verbose)

# Run ISS simulation
if args.steps == "all" or re.match("iss_sim", args.steps):
  iss_sim(matched_list, args.o, args.iss, args.iss_yaml, args.isa, args.verbose)

# Compare ISS simulation result
if args.steps == "all" or re.match("iss_cmp", args.steps):
  iss_cmp(matched_list, args.iss, args.o, args.verbose)
