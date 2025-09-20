// random-image.js

// Array of images and captions (with italic styling).
const imagesWithCaptions = [
  {
    src: 'img/Robut.png',
    caption: 'Quinn Lincoln, <em>"RedGuides Logo"</em>, 2012.',
  },
  {
    src: 'img/Creature-from-the-black-lagoon.jpeg',
    caption: 'Maskoi, <em>"KissAssist 10 Creature from the Black Lagoon Edition"</em>, 2017.',
  },
  {
    src: 'img/Redguidesrelaunch.png',
    caption: '“The front page of RedGuides.com”, <em>RedGuides Relaunch</em>, 2014.',
  },
  {
    src: 'img/Mq2shaman.png',
    caption: 'Sic, <em>"MQ2Shaman Logo"</em>, 2020.',
  },
  {
    src: 'img/Redgnome.png',
    caption: 'Mady G, <em>"RedGnome"</em>, 2022.',
  },
];

// Pick a random entry
const randomIndex = Math.floor(Math.random() * imagesWithCaptions.length);
const chosenImage = imagesWithCaptions[randomIndex];

// Locate the placeholder in your HTML/Markdown where the random image should appear
const placeholder = document.getElementById('random-image-spot');

// If placeholder exists, add <figure> with <img> and <figcaption>
if (placeholder) {
  const figure = document.createElement('figure');
  figure.setAttribute('markdown', '1');
  // Add a style for centering
  figure.style.textAlign = 'center';

  const link = document.createElement('a');
  link.href = chosenImage.src;
  link.classList.add('glightbox');
  link.setAttribute('data-gallery', 'gallery');
  link.setAttribute('data-title', chosenImage.caption);

  const img = document.createElement('img');
  img.src = chosenImage.src;
  img.alt = 'Random EverQuest Image';
  img.setAttribute('loading', 'lazy');
  img.setAttribute('width', '300');

  const figcaption = document.createElement('figcaption');
  figcaption.innerHTML = chosenImage.caption;

  link.appendChild(img);
  figure.appendChild(link);
  figure.appendChild(figcaption);
  placeholder.appendChild(figure);

  // Create hidden links for the rest of the gallery
  const hiddenLinksDiv = document.createElement('div');
  hiddenLinksDiv.style.display = 'none';  // Hide these links
  
  imagesWithCaptions.forEach((image, index) => {
    // Skip the one we already showed
    if (index !== randomIndex) {
      const hiddenLink = document.createElement('a');
      hiddenLink.href = image.src;
      hiddenLink.classList.add('glightbox');
      hiddenLink.setAttribute('data-gallery', 'gallery');
      hiddenLink.setAttribute('data-title', image.caption);
      hiddenLinksDiv.appendChild(hiddenLink);
    }
  });
  
  placeholder.appendChild(hiddenLinksDiv);

  // Initialize GLightbox with some nice options
  const lightbox = GLightbox({
    loop: true,           // Loop back to first image
    touchNavigation: true,
    closeEffect: 'fade',
    slideEffect: 'slide'
  });
}