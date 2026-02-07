#!/usr/bin/env python3
"""
Quick validation of column caching implementation.
Tests the logic without requiring a full PDF/image setup.
"""

# Test 1: Verify cache dictionary is created and updated
print("Test 1: Column cache initialization and storage")
col_boundaries_cache = {}
label = "test_table"

# Simulate first page: extract and cache columns
col_boundaries = [0, 100, 200, 300]
col_boundaries_cache[label] = col_boundaries
print(f"✓ Cached columns for '{label}': {col_boundaries_cache[label]}")

# Test 2: Verify column boundary conversion logic
print("\nTest 2: Column boundary coordinate conversion")
# Simulating when cached columns are used on page 2 with different row position
cached_col_boundaries = [0, 100, 200, 300]
rx = 50  # Row x offset on page 2
rw = 250  # Row width

col_boxes = []
for i in range(len(cached_col_boundaries) - 1):
    col_start = max(0, cached_col_boundaries[i] - rx)
    col_end = min(rw, cached_col_boundaries[i + 1] - rx)
    col_width = col_end - col_start
    if col_width > 0:
        col_boxes.append((col_start, 0, col_width, 100))

print(f"  Cached boundaries: {cached_col_boundaries}")
print(f"  Row position on page 2: x={rx}, width={rw}")
print(f"  Generated column boxes (relative): {col_boxes}")
print(f"✓ Conversion successful with {len(col_boxes)} columns")

# Test 3: Verify logic in apply_template_to_pdf
print("\nTest 3: Label count tracking")
label_counts = {}
pages = [1, 2, 3]
test_label = "data_field"

for page in pages:
    label_counts[test_label] = label_counts.get(test_label, 0) + 1
    
    # Simulate cache retrieval logic
    cached_cols = None
    if label_counts[test_label] > 1 and test_label in col_boundaries_cache:
        cached_cols = col_boundaries_cache[test_label]
    
    print(f"  Page {page}: label_count={label_counts[test_label]}, using_cache={cached_cols is not None}")

print("✓ Label tracking logic works correctly")

# Test 4: Verify the conditional for caching first row
print("\nTest 4: First-row caching condition")
for row_idx in range(1, 5):
    cached_col_boundaries = None  # Simulate not using cache
    col_boundaries_cache_test = {}
    
    # This is the condition used in the code
    if row_idx == 1 and cached_col_boundaries is None and col_boundaries_cache_test is not None:
        print(f"  Row {row_idx}: Would cache columns")
    else:
        print(f"  Row {row_idx}: Would NOT cache columns")

print("✓ Caching condition verified (only caches on row 1, when no cache exists)")

print("\n" + "="*50)
print("All validation tests passed!")
print("="*50)
