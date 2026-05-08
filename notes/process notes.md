- Merged library
- Added other repos
-	Created new process/flow folders and organized processes/flows based on database
-	Identified overlapping eLCI flows/processes in USLCI and move them to eLCI parent folder
-	Connected bridge processes to eLCI/USLCI/forestry
-	Automatically set providers for providers where only one selection is available (i.e., USEEIO bridges) using script below
```
SELECT 
    p.name AS process_name,
    e.id AS exchange_id,
    f.name AS flow_name,
    f.flow_type,
    c.name AS flow_category
FROM tbl_exchanges e
JOIN tbl_flows f ON e.f_flow = f.id
JOIN tbl_processes p ON e.f_owner = p.id
LEFT JOIN tbl_categories c ON f.f_category = c.id
WHERE f.flow_type IN ('PRODUCT_FLOW', 'WASTE_FLOW')
  AND e.is_input = 1
  AND e.f_default_provider = 0;
```
