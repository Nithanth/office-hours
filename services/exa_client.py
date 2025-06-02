from exa_py import Exa
from typing import List, Dict, Any, Optional
import sys
import os
import re
import logging

# Initialize logger
logger = logging.getLogger(__name__)

# Attempt relative import for normal package use (e.g., when app.py imports this)
try:
    from ..config import config
    from ..models.paper import Paper # Import Paper model
except ImportError:
    # Fallback for direct script execution (e.g., python -m services.exa_client)
    # This adds the project root (lux_scholar) to sys.path
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if PROJECT_ROOT not in sys.path:
        sys.path.append(PROJECT_ROOT)
    # Now that PROJECT_ROOT is in sys.path, we can do direct imports
    from config import config
    from models.paper import Paper # Import Paper model

class ExaClient:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Exa API key is required.")
        self.client = Exa(api_key=api_key)

    def search_papers(
        self,
        query: str,
        num_results: int = 10,
        type: str = "auto", # 'neural' for semantic search, 'keyword' for traditional, "auto" for best
        use_autoprompt: bool = True, # Let Exa optimize the query
        category: str = "research_paper", # Focus on research papers
        start_published_date: Optional[str] = None, # Filter by start date
        end_published_date: Optional[str] = None, # Filter by end date
        include_domains: Optional[List[str]] = None, # Filter by domain
        exclude_domains: Optional[List[str]] = None, # Exclude domains
        include_text_list: Optional[List[str]] = None, # Include text list
        exclude_text_list: Optional[List[str]] = None, # Exclude text list
    ) -> List[Paper]: # Changed return type to List[Paper] and added new parameters
        """
        Searches for research papers using the Exa API and returns them as Paper objects.

        Args:
            query: The search query string (e.g., a research topic).
            num_results: The desired number of search results.
            type: The type of search ('neural' or 'keyword' or 'auto').
            use_autoprompt: Whether to let Exa optimize the query.
            category: The category of content to search for.

        Returns:
            A list of Paper Pydantic model instances.
        """
        papers: List[Paper] = []
        try:
            # Use search_and_contents to get results and their full text in one call
            search_response = self.client.search_and_contents(
                query,
                num_results=num_results,
                use_autoprompt=use_autoprompt,
                type=type, # Default is 'neural' in Exa API, here we match previous behavior
                category=category, # Re-enable category filtering
                text=True # Request full text content
            )
            
            # The structure of search_response.results from search_and_contents might be directly usable.
            # Each item in search_response.results should now have a .text field with the full content.
            # It should also hopefully have .author, .id, .title, .url, .published_date.
            
            for result in search_response.results:
                # --- DEBUG: Print the raw result object and its attributes ---
                print(f"--- Raw Exa Result (ID: {result.id or result.url}) from search_and_contents ---")
                try:
                    print(f"All attributes: {dir(result)}")
                    if hasattr(result, 'author'): print(f"Raw author (singular): {result.author}")
                    else: print("Raw author (singular): Not found")
                    if hasattr(result, 'text'): print(f"Raw text (for abstract, should be full content): {result.text[:200]}...") # Print snippet
                    else: print("Raw text (for abstract): Not found")
                    if hasattr(result, 'published_date'): print(f"Raw published_date: {result.published_date}")
                    else: print("Raw published_date: Not found")
                except Exception as e_print:
                    print(f"Error printing result attributes: {e_print}")
                print("--------------------------------------------------")
                # --- END DEBUG ---

                authors_list = []
                if hasattr(result, 'author') and result.author:
                    authors_list = [name.strip() for name in result.author.split(',') if name.strip()] if ',' in result.author else [result.author]
                
                abstract_content = None
                if hasattr(result, 'text') and result.text:
                    abstract_content = result.text # This should now be the full text content

                paper_data = {
                    "id": result.id if result.id else result.url, 
                    "title": result.title if result.title else "No Title Provided",
                    "url": result.url,
                    "authors": authors_list if authors_list else [],
                    "abstract": abstract_content,
                    "published_date": result.published_date if result.published_date else None,
                    "score": result.score if hasattr(result, 'score') else None, # Populate score
                    "source": "Exa (search_and_contents)" # Updated source
                }
                try:
                    papers.append(Paper(**paper_data))
                except Exception as pydantic_error: # Catch Pydantic validation errors for individual papers
                    print(f"Error creating Paper object for result {result.id or result.url}: {pydantic_error}")
                    print(f"Problematic data: {paper_data}")
            
            return papers
        except Exception as e:
            print(f"Error during Exa API search or paper model creation: {e}") 
            return [] # Return empty list on error

    def is_url(self, text: str) -> bool:
        """
        Check if the given text is a URL.
        
        Args:
            text: The text to check.
            
        Returns:
            True if the text appears to be a URL, False otherwise.
        """
        # Simple URL detection - checks for common URL patterns
        url_pattern = re.compile(
            r'^(https?:\/\/)?' # http:// or https:// (optional)
            r'([\da-z\.-]+)\.([a-z\.]{2,6})' # domain
            r'([\/\w \.-]*)*\/?$', # path (optional)
            re.IGNORECASE
        )
        return bool(url_pattern.match(text.strip()))
    
    def find_similar_papers(
        self,
        paper_url: str,
        num_results: int = 5,
        category: str = "research_paper",
    ) -> List[Paper]:
        """
        Find papers similar to the one at the given URL using Exa's find_similar_and_contents endpoint.
        
        Args:
            paper_url: URL of the paper to find similar papers for.
            num_results: Number of similar papers to return.
            category: The category of content to search for.
            
        Returns:
            A list of Paper objects representing similar papers.
        """
        papers: List[Paper] = []
        try:
            # Use find_similar_and_contents to get similar papers and their content
            similar_response = self.client.find_similar_and_contents(
                paper_url,
                num_results=num_results,
                category=category,
                text=True  # Request full text content
            )
            
            # Process the response similar to search_papers
            for result in similar_response.results:
                authors_list = []
                if hasattr(result, 'author') and result.author:
                    authors_list = [name.strip() for name in result.author.split(',') if name.strip()] if ',' in result.author else [result.author]
                
                abstract_content = None
                if hasattr(result, 'text') and result.text:
                    abstract_content = result.text
                
                paper_data = {
                    "id": result.id if result.id else result.url,
                    "title": result.title if result.title else "No Title Provided",
                    "url": result.url,
                    "authors": authors_list if authors_list else [],
                    "abstract": abstract_content,
                    "published_date": result.published_date if result.published_date else None,
                    "score": result.score if hasattr(result, 'score') else None,
                    "source": "Exa (similar papers)"
                }
                try:
                    papers.append(Paper(**paper_data))
                except Exception as pydantic_error:
                    print(f"Error creating Paper object for similar result {result.id or result.url}: {pydantic_error}")
                    print(f"Problematic data: {paper_data}")
            
            return papers
        except Exception as e:
            print(f"Error during Exa API similar papers search: {e}")
            return []  # Return empty list on error

# Example Usage (for direct testing of this module):
if __name__ == "__main__":
    if not config.EXA_API_KEY:
        print("EXA_API_KEY not found in environment. Please set it in your .env file.")
    else:
        print(f"Using EXA_API_KEY: {config.EXA_API_KEY[:5]}...")
        exa_search_client = ExaClient(api_key=config.EXA_API_KEY)
        
        test_query = "AI applications in renewable energy forecasting"
        print(f"\nSearching for: '{test_query}'")
        found_papers = exa_search_client.search_papers(test_query, num_results=3)
        
        if found_papers:
            print(f"\nFound {len(found_papers)} papers (as Paper objects):")
            for i, paper_obj in enumerate(found_papers):
                print(f"  {i+1}. Title: {paper_obj.title}")
                print(f"     URL: {paper_obj.url}")
                print(f"     ID: {paper_obj.id}")
                print(f"     Source: {paper_obj.source}")
                # print(f"     Abstract: {paper_obj.abstract[:100]}..." if paper_obj.abstract else "N/A")
                print("---")
        else:
            print("No papers found or an error occurred.")
