import re
import requests
import json
from bs4 import BeautifulSoup
from dataclasses import dataclass, field, asdict
from typing import List, Dict
from datetime import datetime, timedelta

# Define the base URL for VOX Cinemas
BASE_URL = "https://ksa.voxcinemas.com"

@dataclass
class Movie:
    slug: str
    identifier: str
    title: str
    description: str         # New description field
    image_url: str
    classification: str
    language: str
    showtimes_url: str
    # Nested structure:
    # {
    #   "2025-02-12": {
    #       "day_of_week": "Wednesday",
    #       "showtimes": {
    #           "Cinema Name": {
    #               "Experience": [ "2:15", "5:00", ... ],
    #               ...
    #           },
    #           ...
    #       }
    #   },
    #   "2025-02-13": { ... },
    # }
    timings: Dict[str, Dict[str, object]] = field(default_factory=dict)

def fetch_page(url: str) -> str:
    """Fetches the HTML content of a given URL."""
    response = requests.get(url)
    response.raise_for_status()
    return response.text

def parse_movies(html: str) -> List[Movie]:
    """
    Parses the 'What’s On' page to extract movie details.
    Adjust the selectors if VOX changes the site structure.
    """
    soup = BeautifulSoup(html, 'html.parser')
    movie_articles = soup.find_all("article", class_="movie-summary")
    movies = []

    for article in movie_articles:
        slug = article.get("data-slug", "").strip()
        identifier = article.get("data-identifier", "").strip()
        title = article.get("data-title", "").strip()

        # Attempt to extract a movie description (adjust the class name as needed)
        description = ""
        desc_tag = article.find("p", class_="movie-description")
        if desc_tag:
            description = desc_tag.get_text(strip=True)

        image_url = ""
        a_tag = article.find("a")
        if a_tag:
            img_tag = a_tag.find("img")
            if img_tag:
                image_url = img_tag.get("data-src", "").strip()

        classification = ""
        class_span = article.find("span", class_="classification")
        if class_span:
            classification = class_span.get_text(strip=True)

        language = ""
        language_p = article.find("p", class_="language")
        if language_p:
            language = language_p.get_text(strip=True).replace("Language:", "").strip()

        showtimes_url = ""
        showtimes_a = article.find("a", string=lambda s: s and "Showtimes" in s)
        if showtimes_a:
            showtimes_url = showtimes_a.get("href", "").strip()

        movie = Movie(
            slug=slug,
            identifier=identifier,
            title=title,
            description=description,
            image_url=image_url,
            classification=classification,
            language=language,
            showtimes_url=showtimes_url
        )
        movies.append(movie)

    return movies

def extract_showtimes(detail_html: str) -> Dict[str, Dict[str, List[str]]]:
    """
    Extracts the cinema -> experience -> times structure
    from a single movie's detail page (for one specific date).
    """
    soup = BeautifulSoup(detail_html, 'html.parser')
    dates_div = soup.find("div", class_="dates")
    if not dates_div:
        return {}

    timings_by_place = {}

    # Look for each cinema name (inside an <h3 class="highlight"> tag)
    for place_header in dates_div.find_all("h3", class_="highlight"):
        place = place_header.get_text(" ", strip=True)
        showtimes_ol = place_header.find_next_sibling("ol", class_="showtimes")
        if not showtimes_ol:
            continue

        experience_dict = {}
        # Each top-level <li> in the <ol> corresponds to an experience
        for li in showtimes_ol.find_all("li", recursive=False):
            strong_tag = li.find("strong")
            if not strong_tag:
                continue
            experience = strong_tag.get_text(" ", strip=True)
            nested_ol = li.find("ol")
            if not nested_ol:
                continue

            timings = []
            for time_li in nested_ol.find_all("li"):
                a_tag = time_li.find("a")
                if a_tag:
                    time_text = a_tag.get_text(" ", strip=True)
                else:
                    time_text = time_li.get_text(" ", strip=True)
                # Attempt to match time patterns (e.g., "12:30")
                time_pattern = re.compile(r'\b\d{1,2}:\d{2}\b')
                found_times = time_pattern.findall(time_text)
                if found_times:
                    timings.extend(found_times)
                else:
                    if time_text:
                        timings.append(time_text)
            experience_dict[experience] = timings

        timings_by_place[place] = experience_dict

    return timings_by_place

def enrich_movie_with_timings_for_dates(
    movie: Movie,
    start_date_str: str = "20250212",
    days_to_check: int = 10
) -> None:
    """
    Loops over a date range for a single Movie object,
    building daily URLs like:
      https://ksa.voxcinemas.com/movies/{movie.slug}?d=YYYYMMDD#showtimes
    and storing the results in movie.timings.
    Each date is stored as a dictionary containing:
      - "day_of_week": e.g., "Wednesday"
      - "showtimes": the cinema->experience->times structure
    The date key is a pretty date string (e.g., "2025-02-12").
    """
    date_format = "%Y%m%d"         # used to build the URL
    output_date_format = "%Y-%m-%d"  # used as the key in our JSON
    start_date = datetime.strptime(start_date_str, date_format)

    # Clear any existing timings data
    movie.timings = {}

    for i in range(days_to_check):
        current_date = start_date + timedelta(days=i)
        date_str = current_date.strftime(date_format)
        pretty_date = current_date.strftime(output_date_format)
        day_of_week = current_date.strftime("%A")
        detail_url = f"{BASE_URL}/movies/{movie.slug}?d={date_str}#showtimes"
        print(f"  => Fetching showtimes for '{movie.title}' on {pretty_date} ({day_of_week})")

        try:
            detail_html = fetch_page(detail_url)
            daily_timings = extract_showtimes(detail_html)
            movie.timings[pretty_date] = {
                "day_of_week": day_of_week,
                "showtimes": daily_timings
            }
        except Exception as e:
            print(f"     [Error] {e}")
            movie.timings[pretty_date] = {
                "day_of_week": day_of_week,
                "showtimes": {}
            }

def save_movies_to_json_file(movies: List[Movie], filename: str = "movies.json") -> None:
    """Saves the movie data (including daily showtimes) to a JSON file in an organized manner."""
    # Convert each Movie into a dictionary using asdict()
    movies_dict = [asdict(movie) for movie in movies]
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(movies_dict, f, indent=2)
    print(f"Data saved to {filename}")

def main():
    """
    Main function to:
      1. Fetch the "What’s On" page listing.
      2. Parse movie details.
      3. For each movie, enrich it with daily showtimes (including day-of-week).
      4. Save all the results to a JSON file.
    """
    whatson_url = BASE_URL + "/movies/whatson"
    print(f"Fetching movie listings from: {whatson_url}")

    try:
        html = fetch_page(whatson_url)
        movies = parse_movies(html)
        print(f"Found {len(movies)} movies.\n")

        # Enrich each movie with daily showtimes.
        # Adjust start_date_str and days_to_check as needed.
        for movie in movies:
            print(f"Enriching '{movie.title}' with daily showtimes...")
            enrich_movie_with_timings_for_dates(
                movie,
                start_date_str="20250212",  # starting date (YYYYMMDD)
                days_to_check=10            # number of consecutive days to check
            )

        # Save all movie data to a JSON file.
        save_movies_to_json_file(movies, filename="movies.json")
    except Exception as e:
        print(f"Error fetching movie data: {e}")

if __name__ == "__main__":
    main()