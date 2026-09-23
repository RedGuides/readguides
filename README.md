# Read📖Guides
When a manual needs more than a post.  

A short link to these docs: [readguides.com](https://readguides.com)  
A long link to these docs: [www.redguides.com/docs](https://www.redguides.com/docs)

## Editing

The "edit" button on any page of the site will ideally take you to the maintainer's repository, even if our version differs a bit. (As of this commit for a few plugins it will take you to a fork.)

## Adding your project's docs

- Write your docs in markdown format ("mkdocs material" if you want to get fancy) and commit them to your repository. I suggest using the ["readguides" branch of MQ2EasyFind](https://github.com/Redbot/MQ2EasyFind/tree/readguides/docs) as a template, especially if you want your work included in references, but you do you.
- At this point you can contact RedGuides staff and ask them to add your project to Read📖Guides. Yay you're done! 

<details>
<summary>... or you can DIY ...</summary>

Every project on the site is one entry in [sources.yml](sources.yml). Add yours and open a pull request:

```yaml
  - slug: mq2yourplugin          # published at readguides.com/projects/mq2yourplugin, lowercase
    repo: https://github.com/you/MQ2YourPlugin.git
    branch: main                 # omit if it's master
    docs_dir: docs               # omit if your docs are in docs/; use . for the repo root
```

A `.meta.yml` in your docs directory tells the "edit" button where to go; copy the one from MQ2EasyFind and adjust the repository name.

</details>

## Running your own version

```
git clone https://github.com/RedGuides/readguides.git
cd readguides
pip install -r requirements.txt
python automation/fetch_sources.py
python gen_pages.py
mkdocs serve
```

`fetch_sources.py` clones every repository in `sources.yml` and copies each project's docs into `docs/projects/`. Use `--only <slug>` to refresh one project.

`docs/projects/` is a build product. You can edit it to preview a change with `mkdocs serve`, but the next fetch overwrites it, so make the real change in the project's own repository.
