import pymysql
import logging
import sys
import os
from sshtunnel import SSHTunnelForwarder
from sshtunnel import BaseSSHTunnelForwarderError
import re
import json
import argparse

# Configuration
SSH_HOST = os.environ.get('REDGUIDES_HOST')
SSH_PORT = 22
SSH_USER = os.environ.get('REDGUIDES_USER')

# Database Configuration
DB_HOST_TUNNELED = 'localhost'
DB_PORT_TUNNELED = 3306
DB_USER = os.environ.get('REDGUIDES_DB_USER')
DB_PASSWORD = os.environ.get('REDGUIDES_DB_PASS')
DB_NAME = os.environ.get('REDGUIDES_DB_NAME')

# XenForo Table/Column Names
DB_POST_TABLE = 'xf_post'
DB_POSTID_COLUMN = 'post_id'
DB_THREADID_COLUMN_IN_POST = 'thread_id'
DB_CONTENT_COLUMN = 'message'

DB_THREAD_TABLE = 'xf_thread'
DB_THREADID_COLUMN = 'thread_id'
DB_THREAD_TITLE_COLUMN = 'title'
DB_THREAD_NODEID_COLUMN = 'node_id'
DB_THREAD_STATE_COLUMN = 'discussion_state'

# Target URL Pattern Configuration
TARGET_BASE_URL = 'redguides.com/docs/'
FORUM_THREAD_BASE_URL = 'https://www.redguides.com/community/threads/'

# Filtering Configuration
EXCLUDED_NODE_IDS = [61, 31]  # Moderator forums
VISIBLE_DISCUSSION_STATE = 'visible'

# Output
OUTPUT_DIR = 'data'
OUTPUT_JSON_FILE = os.path.join(OUTPUT_DIR, 'thread_links.json')

# Simple logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

def generate_map():
    """Connects to the DB, scans posts, and generates the discussion map."""
    print("Starting discussion map generation...")

    # Validate required environment variables
    required_env_vars = {
        'REDGUIDES_HOST': SSH_HOST,
        'REDGUIDES_USER': SSH_USER,
        'REDGUIDES_DB_USER': DB_USER,
        'REDGUIDES_DB_PASS': DB_PASSWORD,
        'REDGUIDES_DB_NAME': DB_NAME
    }
    missing_vars = [k for k, v in required_env_vars.items() if not v]
    if missing_vars:
        print(f"Missing required environment variables: {', '.join(missing_vars)}")
        sys.exit(1)

    # Regex pattern for matching documentation URLs
    # Captures the path after /docs/, stopping at anchors, whitespace, or special chars
    url_pattern = re.compile(
        r'(?:https?://)?'                        # Optional protocol
        r'(?:www\.)?'                            # Optional www.
        r'redguides\.com/docs/'                  # Base URL
        r'([^#\s\'"<>\[\]]+)',                   # Capture path (stop at anchor, whitespace, quotes, brackets)
        re.IGNORECASE
    )

    discussion_map = {}  # Structure: { "page/path": {"threads": [list_of_thread_dicts], "seen_threads": set()} }
    thread_info_cache = {}  # Cache thread titles to reduce DB queries: {thread_id: title}

    try:
        print(f"Establishing SSH tunnel to {SSH_HOST}...")
        
        with SSHTunnelForwarder(
            (SSH_HOST, SSH_PORT),
            ssh_username=SSH_USER,
            remote_bind_address=(DB_HOST_TUNNELED, DB_PORT_TUNNELED)
        ) as tunnel:
            local_bind_port = tunnel.local_bind_port
            print(f"SSH tunnel established on local port {local_bind_port}")
            print(f"Connecting to database '{DB_NAME}'...")

            connection = None
            try:
                connection = pymysql.connect(
                    host='127.0.0.1',
                    port=local_bind_port,
                    user=DB_USER,
                    password=DB_PASSWORD,
                    database=DB_NAME,
                    charset='utf8mb4',
                    connect_timeout=30,
                    cursorclass=pymysql.cursors.DictCursor
                )

                with connection.cursor() as post_cursor:
                    with connection.cursor() as thread_cursor:
                        print("Querying posts for documentation URLs...")
                        post_query = f"""
                            SELECT `{DB_POSTID_COLUMN}`, `{DB_THREADID_COLUMN_IN_POST}`, `{DB_CONTENT_COLUMN}`
                            FROM `{DB_POST_TABLE}`
                            WHERE `{DB_CONTENT_COLUMN}` LIKE %s
                        """
                        like_pattern = f"%{TARGET_BASE_URL}%"
                        post_cursor.execute(post_query, (like_pattern,))

                        all_posts = post_cursor.fetchall()
                        total_posts_found = len(all_posts)
                        print(f"Found {total_posts_found} posts containing documentation URLs")

                        processed_count = 0
                        links_found_count = 0
                        
                        for post in all_posts:
                            processed_count += 1
                            post_id = post[DB_POSTID_COLUMN]
                            thread_id = post[DB_THREADID_COLUMN_IN_POST]
                            content = post[DB_CONTENT_COLUMN]

                            if not content or not thread_id:
                                continue

                            if processed_count % 1000 == 0:
                                print(f"Processed {processed_count}/{total_posts_found} posts...")

                            # Find all documentation URLs in this post
                            matches = url_pattern.finditer(content)
                            for match in matches:
                                links_found_count += 1
                                page_path = match.group(1)
                                
                                # Normalize the path: strip trailing slashes, convert to lowercase
                                page_path = page_path.rstrip('/').lower()
                                
                                # Skip empty paths
                                if not page_path:
                                    continue

                                # Get thread title (use cache if available)
                                if thread_id not in thread_info_cache:
                                    thread_query = f"""
                                        SELECT `{DB_THREAD_TITLE_COLUMN}`, `{DB_THREAD_NODEID_COLUMN}`, `{DB_THREAD_STATE_COLUMN}`
                                        FROM `{DB_THREAD_TABLE}`
                                        WHERE `{DB_THREADID_COLUMN}` = %s
                                    """
                                    thread_cursor.execute(thread_query, (thread_id,))
                                    thread_result = thread_cursor.fetchone()
                                    if thread_result:
                                        thread_info_cache[thread_id] = thread_result
                                    else:
                                        thread_info_cache[thread_id] = None
                                        continue

                                thread_info = thread_info_cache[thread_id]
                                if thread_info is None:
                                    continue

                                thread_title = thread_info[DB_THREAD_TITLE_COLUMN]
                                node_id = thread_info[DB_THREAD_NODEID_COLUMN]
                                discussion_state = thread_info[DB_THREAD_STATE_COLUMN]

                                # Filter out excluded forums and non-visible threads
                                if node_id in EXCLUDED_NODE_IDS or discussion_state != VISIBLE_DISCUSSION_STATE:
                                    continue

                                # Initialize entry in map if first time seeing this page path
                                if page_path not in discussion_map:
                                    discussion_map[page_path] = {"threads": [], "seen_threads": set()}

                                # Add thread info if not already added for this specific page
                                if thread_id not in discussion_map[page_path]["seen_threads"]:
                                    thread_url = f"{FORUM_THREAD_BASE_URL.rstrip('/')}/{thread_id}/post-{post_id}"
                                    thread_data = {
                                        "thread_title": thread_title,
                                        "thread_url": thread_url,
                                        "post_id": post_id  # Track post_id for sorting
                                    }
                                    discussion_map[page_path]["threads"].append(thread_data)
                                    discussion_map[page_path]["seen_threads"].add(thread_id)
                                # If we've seen this thread before, update to the highest post_id
                                else:
                                    # Find the existing thread entry and update if this post_id is higher
                                    for thread_data in discussion_map[page_path]["threads"]:
                                        if thread_data["thread_url"].startswith(f"{FORUM_THREAD_BASE_URL.rstrip('/')}/{thread_id}/"):
                                            # Extract current post_id from URL
                                            current_post_id = int(thread_data["thread_url"].split("post-")[-1])
                                            if post_id > current_post_id:
                                                # Update to the newer post
                                                thread_data["thread_url"] = f"{FORUM_THREAD_BASE_URL.rstrip('/')}/{thread_id}/post-{post_id}"
                                                thread_data["post_id"] = post_id
                                            break

                print(f"\nProcessing complete!")
                print(f"Found {links_found_count} total documentation URL references")
                print(f"Mapped to {len(discussion_map)} unique documentation pages")

                # Clean up and process the map:
                # 1. Sort threads by post_id (highest/newest first)
                # 2. Limit to top 10 discussions per page
                # 3. Remove the temporary 'seen_threads' sets and post_id field
                final_map = {}
                for key, value in discussion_map.items():
                    # Sort by post_id descending (newest first)
                    sorted_threads = sorted(value["threads"], key=lambda x: x["post_id"], reverse=True)
                    # Limit to top 10
                    top_threads = sorted_threads[:10]
                    # Remove post_id field (only needed for sorting)
                    for thread in top_threads:
                        del thread["post_id"]
                    final_map[key] = top_threads

                # Save the map to JSON
                print(f"Saving discussion map to {OUTPUT_JSON_FILE}...")
                os.makedirs(OUTPUT_DIR, exist_ok=True)
                with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f:
                    json.dump(final_map, f, indent=4, ensure_ascii=False)
                
                print(f"✅ Discussion map saved successfully!")
                print(f"   Total pages with discussions: {len(final_map)}")

            except pymysql.MySQLError as err:
                print(f"❌ Database error: {err}")
                sys.exit(1)
            except Exception as e:
                print(f"❌ Unexpected error: {e}")
                import traceback
                traceback.print_exc()
                sys.exit(1)
            finally:
                if connection:
                    connection.close()

    except BaseSSHTunnelForwarderError as tunnel_err:
        print(f"❌ SSH Tunnel Error: {tunnel_err}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Connection error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

# --- Main Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate a map of forum discussions linking to MkDocs pages.')
    args = parser.parse_args()
    generate_map()
    sys.exit(0)
