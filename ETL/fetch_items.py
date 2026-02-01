"""
Function to fetch items from CS Market API and save hash_name and name to JSON file.
"""
import json
import os
import re
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Tuple
from urllib.parse import quote
from dotenv import load_dotenv

load_dotenv()


def fetch_items(api_key: str, output_file: str = "items.json") -> Optional[List[Dict[str, str]]]:
    """
    Fetches items from CS Market API and saves hash_name, name, weapon, quality, type, and collection to a JSON file.
    
    Args:
        api_key: API key for authentication
        output_file: Path to the output JSON file (default: "items.json")
    
    Returns:
        List of dictionaries containing hash_name, name, weapon, quality, type, and collection, or None if request fails
    """
    url = "https://api.csmarketapi.com/v1/items/"
    headers = {"accept": "application/json"}
    
    try:
        response = requests.get(url, headers=headers, params={"key": api_key})
        response.raise_for_status()
        
        data = response.json()
        excluded_types = {"sticker", "graffiti", "container"}
        extracted_data = [
            {
                "hash_name": item.get("hash_name", ""),
                "name": item.get("market_hash_name", ""),
                "weapon": item.get("weapon", ""),
                "quality": item.get("quality", ""),
                "type": item.get("type", ""),
                "collection": item.get("collection", ""),
                "link": f"https://csgostocks.de/charts/item?market_hash_name={quote(item.get('market_hash_name', ''))}"
            }
            for item in data
            if item.get("type", "").lower() not in excluded_types
        ]
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(extracted_data, f, indent=2, ensure_ascii=False)
        
        print(f"Successfully fetched {len(extracted_data)} items and saved to {output_file}")
        return extracted_data
        
    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error: {e}")
        if hasattr(e.response, 'status_code') and e.response.status_code == 401:
            print("Authentication failed. Please check your API key.")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON response: {e}")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None


def fetch_price_data(item_name_for_path: str, name: Optional[str] = None, category: Optional[str] = None, exterior: Optional[str] = None, item_type: Optional[str] = None, silent: bool = False) -> Optional[Dict]:
    """
    Fetches price data from csgostocks.com API for a given item.
    
    Args:
        item_name_for_path: The item name for URL path (e.g., "★ Huntsman Knife | Stained (Battle-Scarred)")
        name: Optional name parameter for query (e.g., "Huntsman Knife | Stained")
        category: Optional category parameter
        exterior: Optional exterior parameter
        item_type: Optional item type (e.g., "Gloves") to determine endpoint
        silent: If True, suppress error messages
    
    Returns:
        Dictionary containing the price data (e.g., {"ohlc": [...]}), or None if request fails
    """
    encoded_name = quote(item_name_for_path, safe='')
    
    if item_type and item_type.lower() == "gloves":
        url = f"https://www.csgostocks.com/api/prices/price/ohlc/{encoded_name}"
        params = None
    else:
        url = f"https://www.csgostocks.com/api/prices/price/{encoded_name}"
        params = {}
        if name:
            params["name"] = name
        if category:
            params["category"] = category
        if exterior:
            params["exterior"] = exterior
    
    headers = {"accept": "application/json"}
    
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError:
        if not silent:
            print(f"HTTP Error fetching price data for: {item_name_for_path[:50]}")
        return None
    except requests.exceptions.RequestException:
        if not silent:
            print(f"Request failed fetching price data for: {item_name_for_path[:50]}")
        return None
    except json.JSONDecodeError:
        if not silent:
            print(f"Failed to parse JSON response for price data: {item_name_for_path[:50]}")
        return None
    except Exception:
        if not silent:
            print(f"An error occurred fetching price data for: {item_name_for_path[:50]}")
        return None


def parse_item_name(full_name: str) -> Tuple[str, Optional[str], Optional[str], str]:
    """
    Parses an item name to extract base name, exterior, category, and URL path name.
    
    Args:
        full_name: Full item name (e.g., "★ StatTrak™ Huntsman Knife | Autotronic (Factory New)")
    
    Returns:
        Tuple of (name_for_query, exterior, category, name_for_path)
    """
    base_name = full_name
    exterior = None
    category = None
    
    match = re.search(r'\s*\(([^)]+)\)\s*$', full_name)
    if match:
        exterior = match.group(1)
        base_name = full_name[:match.start()].strip()
    
    has_star = base_name.startswith("★")
    has_stattrak = "StatTrak" in base_name or "™" in base_name
    
    name_for_path = base_name
    name_for_query = base_name
    
    if has_star:
        category = "★"
        name_for_query = base_name.replace("★", "", 1).strip()
    
    if has_stattrak:
        name_for_path = re.sub(r'StatTrak™?\s*', '', name_for_path)
        name_for_query = re.sub(r'StatTrak™?\s*', '', name_for_query)
    
    if base_name.startswith("Souvenir "):
        name_for_query = name_for_query.replace("Souvenir ", "", 1)
        name_for_path = name_for_path.replace("Souvenir ", "", 1)
        if not category:
            category = "Souvenir"
    
    if exterior:
        name_for_path = f"{name_for_path} ({exterior})"
    
    return name_for_query, exterior, category, name_for_path


def fetch_keyfigures_data(item_name: str, silent: bool = False) -> Optional[Dict]:
    """
    Fetches keyfigures data from csgostocks.com API for a given item.
    
    Args:
        item_name: The full item name (e.g., "★ Huntsman Knife | Stained (Battle-Scarred)")
        silent: If True, suppress error messages
    
    Returns:
        Dictionary containing the keyfigures data, or None if request fails
    """
    encoded_name = quote(item_name, safe='')
    url = f"https://www.csgostocks.com/api/prices/price/keyfigures/{encoded_name}"
    headers = {"accept": "application/json"}
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError:
        if not silent:
            print(f"HTTP Error fetching keyfigures data for: {item_name[:50]}")
        return None
    except requests.exceptions.RequestException:
        if not silent:
            print(f"Request failed fetching keyfigures data for: {item_name[:50]}")
        return None
    except json.JSONDecodeError:
        if not silent:
            print(f"Failed to parse JSON response for keyfigures data: {item_name[:50]}")
        return None
    except Exception:
        if not silent:
            print(f"An error occurred fetching keyfigures data for: {item_name[:50]}")
        return None


def run_pipeline(api_key: str, output_file: str = "items.json") -> Optional[List[Dict]]:
    """
    Main pipeline that fetches items from REST API and enriches them with csgostocks.com data.
    
    Args:
        api_key: API key for CS Market API authentication
        output_file: Path to the output JSON file (default: "items.json")
    
    Returns:
        List of enriched item dictionaries, or None if pipeline fails
    """
    print("Step 1: Fetching items from CS Market API...")
    items = fetch_items(api_key, output_file)
    
    if not items:
        print("Failed to fetch items from REST API. Pipeline stopped.")
        return None
    
    print(f"Step 2: Enriching {len(items)} items with csgostocks.com data using 50 workers...")
    
    lock = threading.Lock()
    success_count = 0
    fail_count = 0
    processed_count = 0
    
    def process_item(item_data: Tuple[int, Dict]) -> Tuple[int, Dict, bool, str]:
        """Process a single item and return the result with its index."""
        idx, item = item_data
        item_name = item.get("name", "")
        
        if not item_name:
            return idx, item, True, "SKIPPED"
        
        try:
            name_for_query, exterior, category, name_for_path = parse_item_name(item_name)
            item_type = item.get("type", "")
            
            price_data = fetch_price_data(name_for_path, name=name_for_query, category=category, exterior=exterior, item_type=item_type, silent=True)
            keyfigures_data = fetch_keyfigures_data(name_for_path, silent=True)
            
            enriched_item = item.copy()
            success = False
            status = "FAILED"
            
            if price_data:
                enriched_item["price_data"] = price_data
                success = True
                status = "SUCCESS"
            
            if keyfigures_data:
                enriched_item["keyfigures_data"] = keyfigures_data
            
            return idx, enriched_item, success, status
        except Exception as e:
            return idx, item, False, f"ERROR: {str(e)}"
    
    enriched_items = [None] * len(items)
    
    with ThreadPoolExecutor(max_workers=50) as executor:
        future_to_item = {executor.submit(process_item, (idx, item)): (idx, item) for idx, item in enumerate(items)}
        
        for future in as_completed(future_to_item):
            idx, enriched_item, success, status = future.result()
            enriched_items[idx] = enriched_item
            
            with lock:
                processed_count += 1
                if success:
                    success_count += 1
                    print(f"Item {idx + 1}/{len(items)}: ✓ SUCCESS - {enriched_item.get('name', '')[:60]}")
                else:
                    fail_count += 1
                    print(f"Item {idx + 1}/{len(items)}: ✗ {status} - {enriched_item.get('name', '')[:60]}")
                
                if processed_count % 100 == 0:
                    print(f"Progress: {processed_count}/{len(items)} items processed ({success_count} successful, {fail_count} failed)")
    
    print(f"Step 3: Saving enriched data to {output_file}...")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(enriched_items, f, indent=2, ensure_ascii=False)
    
    print(f"Pipeline completed successfully. {len(enriched_items)} items saved to {output_file}")
    return enriched_items


if __name__ == "__main__":
    API_KEY = os.getenv("CSMARKETAPI_KEY")
    
    if not API_KEY:
        print("Error: CSMARKETAPI_KEY not found in environment variables or .env file")
        exit(1)
    
    run_pipeline(API_KEY)
