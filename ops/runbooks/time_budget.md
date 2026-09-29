# A time limit stopped the read

A partial run keeps its completed answers. Inputs without completed answers remain due.
For example, the artist catalog checks its budget before each artist. A cancelled lookup
stops the read even when the run has time left. It rejects no unfinished artists.
The next daily cycle reads the remaining artists for the week.

Open the function page to inspect completed inputs and load receipts. Check progress with:

```sh
pnpm --dir control mdp status mb_artist_catalog
```
