-- Drop table if it already exists

DROP TABLE IF EXISTS bronze.state_code;
 
-- Create table

CREATE TABLE bronze.state_code (

    state_id INT PRIMARY KEY,

    state_name VARCHAR(100) NOT NULL UNIQUE,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP

);
 
-- Insert only Indian States (excluding Union Territories)

INSERT INTO bronze.state_code (state_id, state_name)

VALUES

(1, 'Jammu & Kashmir'),

(2, 'Himachal Pradesh'),

(3, 'Punjab'),

(5, 'Uttarakhand'),

(6, 'Haryana'),

(8, 'Rajasthan'),

(9, 'Uttar Pradesh'),

(10, 'Bihar'),

(11, 'Sikkim'),

(12, 'Arunachal Pradesh'),

(13, 'Nagaland'),

(14, 'Manipur'),

(15, 'Mizoram'),

(16, 'Tripura'),

(17, 'Meghalaya'),

(18, 'Assam'),

(19, 'West Bengal'),

(20, 'Jharkhand'),

(21, 'Odisha'),

(22, 'Chhattisgarh'),

(23, 'Madhya Pradesh'),

(24, 'Gujarat'),

(27, 'Maharashtra'),

(29, 'Karnataka'),

(30, 'Goa'),

(32, 'Kerala'),

(33, 'Tamil Nadu'),

(36, 'Telangana'),

(37, 'Andhra Pradesh');
 