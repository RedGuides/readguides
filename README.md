# Read📖Guides
When a manual needs more than a post.  

A short link to these docs: [readguides.com](https://readguides.com)  
A long link to these docs: [www.redguides.com/docs](https://www.redguides.com/docs)

## Editing

The "edit" button on any page of the site will ideally take you to the maintainer's repository, even if our version differs a bit. (As of this commit for MQ and a few others it will take you to a fork.)

## Adding your project's docs

- Write your docs in markdown format ("mkdocs material" if you want to get fancy) and commit them to your repository. I suggest using the ["readguides" branch of MQ2EasyFind](https://github.com/Redbot/MQ2EasyFind/tree/readguides/docs) as a template, especially if you want your work included in references, but you do you.
- At this point you can contact RedGuides staff and ask them to add your project to Read📖Guides. Yay you're done! 

<details>
<summary>... or you can DIY ...</summary>

- Fork this repo.
    - If your docs are in the root of your project's repository, add them as a submodule in the docs/projects/ directory.
    - If your docs are in a subdirectory of your repository, you'll need to add them as a submodule in our vendor/ directory, and then symlink them to the docs/projects/ directory. Again, we're happy to do this for you.
- Open a pull request.

</details>

## Running your own version

The submodules in this repo require symlinks. If you're on a Microsoft product make sure to run the following command to enable them,
```bash
git config core.symlinks true
```

run `python gen_pages.py` to generate some of the index files, and then you're ready to roll! Start a virtual environment -> `pip install -r requirements.txt` -> `mkdocs serve`