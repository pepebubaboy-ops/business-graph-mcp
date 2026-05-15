CREATE CONSTRAINT business_node_id IF NOT EXISTS
FOR (n:BusinessNode) REQUIRE n.id IS UNIQUE;

CREATE INDEX business_node_workspace IF NOT EXISTS
FOR (n:BusinessNode) ON (n.workspace_id);
