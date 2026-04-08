import sys
import os
import time
import subprocess
import argparse

def run_step(step_num, name, cmd):
    print(f"\n{'='*60}")
    print(f"STEP {step_num}: {name}")
    print(f"{'='*60}")
    
    start_time = time.time()
    try:
        # Run the command and stream output to the console
        result = subprocess.run(cmd, check=True)
        elapsed = time.time() - start_time
        print(f"\n[+] Step {step_num} completed successfully in {elapsed:.2f} seconds.")
        return True, elapsed
    except subprocess.CalledProcessError as e:
        elapsed = time.time() - start_time
        print(f"\n[-] Step {step_num} FAILED after {elapsed:.2f} seconds.")
        print(f"    Command: {' '.join(cmd)}")
        print(f"    Return code: {e.returncode}")
        return False, elapsed
    except FileNotFoundError as e:
        elapsed = time.time() - start_time
        print(f"\n[-] Step {step_num} FAILED: Command not found.")
        print(f"    Error: {e}")
        print("    Ensure AMPL is installed and added to your system PATH.")
        return False, elapsed

def main():
    input_excel = "data/CIGRE_HV_Network_Input.xlsx"

    if not os.path.exists(input_excel):
        print(f"Error: Input Excel file '{input_excel}' does not exist.")
        print("Please ensure your file is named exactly 'CIGRE_HV_Network_Input.xlsx' and placed in the 'data' folder.")
        sys.exit(1)

    # Define the pipeline steps
    steps = [
        (1, "Generate network.dat", 
            [sys.executable, "python/01_excel_to_dat.py"]),
        
        (2, "Validate network.dat", 
            [sys.executable, "python/02_dat_validator.py"]),
        
        (3, "Solve MPEC in AMPL", 
            ["ampl", "ampl/04_stackelberg_kkt.run"]),
        
        (4, "Extract Results", 
            [sys.executable, "python/05_results_extractor.py"]),
        
        (5, "Generate Excel Report", 
            [sys.executable, "python/06_results_to_excel.py"]),
        
        (6, "Verify KKT Conditions", 
            [sys.executable, "python/07_kkt_verifier.py"])
    ]

    total_start = time.time()
    
    print("\n" + "★"*60)
    print("STACKELBERG DUAL-PRICE REACTIVE POWER MARKET PIPELINE")
    print("★"*60)
    print(f"Input file: {input_excel}")
    
    for step_num, name, cmd in steps:
        success, _ = run_step(step_num, name, cmd)
        if not success:
            print(f"\n{'*'*60}")
            print(f"PIPELINE FAILURE: Stopped at Step {step_num} ({name})")
            print(f"{'*'*60}")
            sys.exit(1)

    total_elapsed = time.time() - total_start
    
    print(f"\n{'*'*60}")
    print(f"PIPELINE SUCCESS!")
    print(f"{'*'*60}")
    print(f"Total execution time: {total_elapsed:.2f} seconds.")
    print(f"Final results saved to: output/stackelberg_results.xlsx")
    print(f"{'*'*60}\n")

if __name__ == "__main__":
    main()
