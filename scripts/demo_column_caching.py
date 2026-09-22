#!/usr/bin/env python3
"""
Demo of column caching behavior across pages.
This demonstrates how the implementation handles multi-page documents.
"""

def demo_column_caching():
    """
    Demonstrates the column caching flow for a 3-page PDF with a data_field region.
    """
    print("=" * 60)
    print("Column Caching Implementation Demo")
    print("=" * 60)
    print()
    
    # Simulate the data structures
    label_counts = {}
    col_boundaries_cache = {}
    
    # Simulate processing 3 pages
    pdf_pages = 3
    region_label = "invoice_table"
    
    for page_num in range(1, pdf_pages + 1):
        print(f"Processing Page {page_num}:")
        print("-" * 40)
        
        # Track label count
        label_counts[region_label] = label_counts.get(region_label, 0) + 1
        count = label_counts[region_label]
        
        # Determine if we should use cache
        cached_cols = None
        if count > 1 and region_label in col_boundaries_cache:
            cached_cols = col_boundaries_cache[region_label]
        
        print(f"  Label count: {count}")
        print(f"  Using cached columns: {cached_cols is not None}")
        
        if cached_cols is None:
            # Auto-detect columns
            print("  Action: AUTO-DETECT columns")
            
            # Simulate column detection on first row
            if page_num == 1:
                # Simulate detected columns: left, middle, right
                detected_cols = [0, 150, 300, 500]  # x-coordinates
                col_boundaries_cache[region_label] = detected_cols
                print(f"  Detected column boundaries: {detected_cols}")
                print("  Cached for future pages")
            
            # Rows would still be auto-detected (adaptive per page)
            print("  Rows: AUTO-DETECTED (adaptive to this page)")
        else:
            # Use cached columns
            print(f"  Action: USE CACHED columns: {cached_cols}")
            print("  Rows: AUTO-DETECTED (adaptive to this page)")
        
        print()
    
    print("=" * 60)
    print("Result Summary:")
    print("=" * 60)
    print(f"Column structure (cached from page 1): {col_boundaries_cache[region_label]}")
    print("All pages use the same column structure (consistent)")
    print("All pages have page-specific row detection (adaptive)")
    print()
    print("Expected Excel output:")
    print("  - All pages: Same column layout")
    print("  - Each page: Rows adapted to content")
    print("  - Data continues sequentially: Page1_rows + Page2_rows + Page3_rows")

if __name__ == "__main__":
    demo_column_caching()
