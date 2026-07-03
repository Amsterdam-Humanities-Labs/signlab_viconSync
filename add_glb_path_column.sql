-- Add glb_path column to vicon_files table
-- This stores the path to the corresponding GLB file for FBX files

USE admin_gebarenoverleg;

ALTER TABLE vicon_files
ADD COLUMN glb_path VARCHAR(1024) NULL COMMENT 'Path to corresponding GLB file in /web/gebarenoverleg_media/fbx' AFTER filename;

-- Add index for faster queries on glb_path
ALTER TABLE vicon_files
ADD INDEX idx_glb_path (glb_path(255));
