import mysql.connector
from dotenv import load_dotenv
import os

load_dotenv()


def get_connection():
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", 3306)),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE"),
    )


def create_tables():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meetings (
            id INT AUTO_INCREMENT PRIMARY KEY,
            title VARCHAR(500),
            filename VARCHAR(500) NOT NULL,
            status VARCHAR(20) DEFAULT 'completed',
            source VARCHAR(20) DEFAULT 'upload',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transcripts (
            id INT AUTO_INCREMENT PRIMARY KEY,
            meeting_id INT NOT NULL,
            content LONGTEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (meeting_id) REFERENCES meetings(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INT AUTO_INCREMENT PRIMARY KEY,
            meeting_id INT NOT NULL,
            title VARCHAR(500) NOT NULL,
            description TEXT,
            assignee VARCHAR(255),
            jira_ticket_id VARCHAR(100),
            jira_worthy BOOLEAN DEFAULT TRUE,
            jira_reason VARCHAR(500),
            module VARCHAR(255) DEFAULT 'General',
            is_grouped BOOLEAN DEFAULT FALSE,
            dismissed BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (meeting_id) REFERENCES meetings(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS code_entities (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            type VARCHAR(50) NOT NULL,
            service VARCHAR(255),
            values_json TEXT,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS index_jobs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            project_name VARCHAR(255) NOT NULL,
            root_path VARCHAR(1000) NOT NULL,
            status VARCHAR(50) DEFAULT 'pending',
            node_count INT DEFAULT 0,
            edge_count INT DEFAULT 0,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS domain_entities (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            project_name VARCHAR(255),
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Add columns if they don't exist
    for mcol, mdef in [("title", "VARCHAR(500) AFTER id"), ("status", "VARCHAR(20) DEFAULT 'completed'"), ("source", "VARCHAR(20) DEFAULT 'upload'")]:
        try:
            cursor.execute(f"ALTER TABLE meetings ADD COLUMN {mcol} {mdef}")
        except mysql.connector.errors.ProgrammingError:
            pass
    for col, definition in [("jira_worthy", "BOOLEAN DEFAULT TRUE"), ("jira_reason", "VARCHAR(500)"), ("module", "VARCHAR(255) DEFAULT 'General'"), ("is_grouped", "BOOLEAN DEFAULT FALSE"), ("dismissed", "BOOLEAN DEFAULT FALSE")]:
        try:
            cursor.execute(f"ALTER TABLE tasks ADD COLUMN {col} {definition}")
        except mysql.connector.errors.ProgrammingError:
            pass
    for col, definition in [("progress", "INT DEFAULT 0"), ("current_step", "VARCHAR(300) DEFAULT ''")]:
        try:
            cursor.execute(f"ALTER TABLE index_jobs ADD COLUMN {col} {definition}")
        except mysql.connector.errors.ProgrammingError:
            pass
    conn.commit()
    cursor.close()
    conn.close()
