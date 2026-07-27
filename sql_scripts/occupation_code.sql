-- Create table
CREATE TABLE bronze.occupation_code (
 
    occupation_id INT PRIMARY KEY,
 
    occupation_name VARCHAR(100) NOT NULL UNIQUE,
 
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
 
);
 
-- Insert occupation master data
INSERT INTO bronze.occupation_code (occupation_id, occupation_name)
VALUES
(1, 'SERVICE'),
(2, 'BUSINESS'),
(3, 'PROFESSIONAL'),
(4, 'AGRICULTURE'),
(5, 'STUDENT'),
(6, 'RETIRED'),
(7, 'HOUSEWIFE'),
(8, 'OTHERS'),
(9, 'PRIVATE SECTOR'),
(10, 'PUBLIC SECTOR'),
(11, 'SELF EMPLOYED'),
(41, 'NOT APPLICABLE');