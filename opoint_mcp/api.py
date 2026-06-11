"""
Module for interacting with the Opoint API.
"""
import logging
import os
from datetime import datetime
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('OpointAPI')

class OpointAPI:
    """
    A wrapper for the Opoint API that provides methods for searching sites and articles.
    """
    
    def __init__(self, api_key: str | None = None):
        """
        Initialize the OpointAPI with an API key.
        
        Args:
            api_key (Optional[str]): API key for authentication. If None, uses the key from environment.
        """
        self.api_key = api_key or os.getenv('OPOINT_API_KEY')
        if not self.api_key:
            raise ValueError("No API key provided and OPOINT_API_KEY not found in environment variables.")
        
        self.base_url = "https://api.opoint.com"
        self.headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
    
    def search_site(self, site_name: str) -> dict[str, Any]:
        """
        Search for a site in the Opoint database.
        
        Args:
            site_name (str): Name of the site to search for
            
        Returns:
            Dict[str, Any]: Response containing site information
        """
        url = f"{self.base_url}/suggest/en_GB_1/single/5/site:0/0/2147483647/nometa/{site_name}"
        
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            
            data = response.json()
            logger.info(f"Found {len(data.get('results', []))} sites matching '{site_name}'")
            return data
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error searching for site '{site_name}': {e!s}")
            return {"error": str(e)}
    
    def search_articles(self, 
                       site_id: str | None = None,
                       search_text: str | None = None,
                       language: str | None = None,
                       num_articles: int = 20,
                       min_score: float | None = None,
                       source: str | None = None,
                       topic_ids: list[str] | None = None,
                       media_topic_ids: list[str] | None = None,
                       start_date: datetime | None = None,
                       end_date: datetime | None = None) -> pd.DataFrame:
        """
        Search for articles from a specific site or across all sites with optional text matching.
        If no site_id is provided, searches across all available sites.
        
        Args:
            site_id (Optional[str]): ID of the site to search in, or None to search all sites
            search_text (Optional[str]): Text to search for in articles
            language (Optional[str]): Language code to filter by (e.g., 'en', 'no')
            num_articles (int): Number of articles to retrieve
            min_score (float): Minimum relevance score threshold (e.g., 0.75)
            source (Optional[str]): Source name to filter by (e.g., 'Reuters', 'Bloomberg')
            start_date (Optional[datetime]): Start date for article search (inclusive)
            end_date (Optional[datetime]): End date for article search (inclusive)
            
        Returns:
            pd.DataFrame: DataFrame containing the matched articles
        """
        url = f"{self.base_url}/search/"
        
        # Build the search query
        search_parts = []
        
        if search_text:
            # Add text search for header, summary, and body
            text_parts = [
                f"header:{search_text}",
                f"summary:{search_text}",
                f"text:{search_text}"
            ]
            search_parts.append(f"({' OR '.join(text_parts)})")
        
        # Add topic ID filters if specified
        if topic_ids:
            topic_parts = [f"topic:1{tid}" for tid in topic_ids]
            if len(topic_parts) == 1:
                search_parts.append(topic_parts[0])
            else:
                search_parts.append(f"({' OR '.join(topic_parts)})")

        # Add media/topic ID filters (media-specific) if specified
        if media_topic_ids:
            media_parts = [f"topic:1{mid}" for mid in media_topic_ids]
            if len(media_parts) == 1:
                search_parts.append(media_parts[0])
            else:
                search_parts.append(f"({' OR '.join(media_parts)})")
        
        # Add site filter only if specified
        if site_id:
            search_parts.append(f"site:{site_id}")
            
        # Add source filter if specified
        if source:
            search_parts.append(f"source:{source}")
            
        # If no search criteria provided, match everything
        searchline = " AND ".join(search_parts) if search_parts else "*"
        
        # Build the request payload
        payload = {
            "expressions": [{
                "linemode": "R",
                "searchline": {
                    "searchterm": searchline,
                    "filters": []
                }
            }],
            "params": {
                "requestedarticles": num_articles,
                "main": {
                    "header": 1,
                    "summary": 1,
                    "text": 1,
                    "first_source": 1,  # Ensure source information is included
                    "othersources": 1,  # Include other sources
                    "matches": 1  # Include search matches for scoring
                }
            }
        }
        
        # Add date filtering if specified
        if start_date:
            payload["params"]["oldest"] = int(start_date.timestamp())
        if end_date:
            payload["params"]["newest"] = int(end_date.timestamp())
        
        # Add language filter if specified
        if language:
            payload["expressions"][0]["searchline"]["filters"].append({
                "type": "lang",
                "id": language
            })
            
        # Add score filter if specified
        if min_score is not None:
            payload["expressions"][0]["searchline"]["filters"].append({
                "type": "score",
                "min": min_score
            })
        
        try:
            response = requests.post(url, headers=self.headers, json=payload)
            response.raise_for_status()
            
            data = response.json()
            articles = data.get("searchresult", {}).get("document", [])
            
            if not articles:
                logger.warning("No articles found matching the search criteria")
                return pd.DataFrame()
            
            # Process articles into DataFrame
            now = datetime.now()
            records = []
            
            for article in articles:
                # Extract all available fields according to API documentation
                record = {
                    # Time fields
                    'published_date': pd.to_datetime(article.get('local_time', {}).get('text')),
                    'unix_timestamp': article.get('unix_timestamp'),
                    'date_added': now,
                    
                    # Content fields
                    'title': article.get('header', {}).get('text', ''),
                    'summary': article.get('summary', {}).get('text', ''),
                    'text': article.get('body', {}).get('text', ''),
                    'author': article.get('author', ''),
                    'word_count': article.get('word_count'),
                    
                    # URL fields
                    'url': article.get('orig_url', ''),  # Original article URL
                    'opoint_url': article.get('url', ''),  # OPOINT tool URL
                    'url_common': article.get('url_common', ''),  # Domain
                    
                    # Source fields
                    'site_name': article.get('first_source', {}).get('sitename', ''),
                    'source_name': article.get('first_source', {}).get('name', ''),
                    'source_url': article.get('first_source', {}).get('url', ''),
                    'site_url': article.get('first_source', {}).get('siteurl', ''),
                    
                    # IDs and metadata
                    'id_site': article.get('id_site'),
                    'id_article': article.get('id_article'),
                    'position': article.get('position'),
                    
                    # Language and location
                    'language': article.get('language', {}).get('text', ''),
                    'country_name': article.get('countryname', ''),
                    'country_code': article.get('countrycode', ''),
                    
                    # Sentiment if available
                    'sentiment': article.get('topics_and_entities', {}).get('sentiment', ''),
                    'sentiment_score': article.get('topics_and_entities', {}).get('sentiment_score'),
                    
                    # Media type info
                    'media_type': article.get('mediatype', {}).get('text', ''),
                    'paywall': article.get('mediatype', {}).get('paywall', False),
                    'fulltext': article.get('mediatype', {}).get('fulltext', False),
                    
                    # Provider info
                    'provider': 'opoint'
                }
                records.append(record)
            
            df = pd.DataFrame(records)
            source_info = "from specified site" if site_id else "across all sites"
            logger.info(f"Retrieved {len(df)} articles {source_info}")
            return df
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error searching for articles: {e!s}")
            return pd.DataFrame()
    
    def search_site_and_articles(self, 
                                site_name: str | None = None,
                                search_text: str | None = None,
                                language: str | None = None,
                                num_articles: int = 20,
                                min_score: float | None = None,
                                source: str | None = None,
                                topic_ids: list[str] | None = None,
                                media_topic_ids: list[str] | None = None,
                                start_date: datetime | None = None,
                                end_date: datetime | None = None) -> pd.DataFrame:
        """
        Combined method to search for articles, optionally from a specific site.
        If no site_name is provided, searches across all sites.
        
        Args:
            site_name (Optional[str]): Name of the site to search for, or None to search all sites
            search_text (Optional[str]): Text to search for in articles
            language (Optional[str]): Language code to filter by
            num_articles (int): Number of articles to retrieve
            min_score (float): Minimum relevance score threshold (e.g., 0.75)
            source (Optional[str]): Source name to filter by (e.g., 'Reuters', 'Bloomberg')
            start_date (Optional[datetime]): Start date for article search (inclusive)
            end_date (Optional[datetime]): End date for article search (inclusive)
            
        Returns:
            pd.DataFrame: DataFrame containing the matched articles
        """
        if site_name is None:
            # Search across all sites
            return self.search_articles(
                search_text=search_text,
                language=language,
                num_articles=num_articles,
                min_score=min_score,
                source=source,
                start_date=start_date,
                topic_ids=topic_ids,
                media_topic_ids=media_topic_ids,
                end_date=end_date
            )
            
        # Search for specific site
        site_response = self.search_site(site_name)
        
        if "error" in site_response:
            logger.error(f"Error searching for site: {site_response['error']}")
            return pd.DataFrame()
        
        sites = site_response.get("results", [])
        if not sites:
            logger.warning(f"No sites found matching '{site_name}'")
            return pd.DataFrame()
        
        # Use the first matching site's ID
        site_id = sites[0].get("id")
        if not site_id:
            logger.error("Site ID not found in response")
            return pd.DataFrame()
        
        # Search for articles from this site
        return self.search_articles(
            site_id=site_id,
            search_text=search_text,
            language=language,
            num_articles=num_articles,
            min_score=min_score,
            source=source,
            topic_ids=topic_ids,
            media_topic_ids=media_topic_ids,
            start_date=start_date,
            end_date=end_date
        )
