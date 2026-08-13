const root = document.querySelector("[data-php-sites-list]");

// The list is database-rendered by the page route. JS only removes the overlay;
// it must not create a second loading request for the same rows.
if (root && window.hideSkeleton) window.hideSkeleton("php-sites-list-skeleton", 0);
