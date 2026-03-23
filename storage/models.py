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
            filename VARCHAR(500) NOT NULL,
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
    # Add columns if they don't exist
    for col, definition in [("jira_worthy", "BOOLEAN DEFAULT TRUE"), ("jira_reason", "VARCHAR(500)"), ("module", "VARCHAR(255) DEFAULT 'General'"), ("is_grouped", "BOOLEAN DEFAULT FALSE"), ("dismissed", "BOOLEAN DEFAULT FALSE")]:
        try:
            cursor.execute(f"ALTER TABLE tasks ADD COLUMN {col} {definition}")
        except mysql.connector.errors.ProgrammingError:
            pass
    conn.commit()
    cursor.close()
    conn.close()
