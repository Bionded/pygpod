/*
 * libgpod reference tool for pygpod's interop tests (tests/test_libgpod_interop.py).
 *
 *   libgpod_tool build <mountpoint> <spec.tsv>   write an iTunesDB with libgpod
 *   libgpod_tool dump  <mountpoint>              print the iTunesDB as libgpod parses it
 *   libgpod_tool dump-spl <mountpoint>           same, after itdb_spl_update_all() (libgpod's members)
 *   libgpod_tool dump-photos <mountpoint>        print the Photo Database as libgpod parses it
 *
 * spec.tsv (tab separated, one record per line):
 *   name     <master playlist name>
 *   track    <file> <title> <artist> <album> <genre> <track_nr> <year> <mediatype> <rating> <playcount>
 *   playlist <name> <podcast 0|1> <comma separated 0-based track indexes>
 *
 * dump output (tab separated):
 *   T <id> <dbid> <title> <artist> <album> <genre> <track_nr> <year> <mediatype> <rating> <playcount>
 *     <ipod_path> <compilation> <albumartist> <bookmark_time> <remember_playback_position>
 *   P <name> <is_mpl> <is_podcasts> <n_members> <member titles joined by '|'>
 *   S <name> <liveupdate> <checkrules> <checklimits> <limittype> <limitsort> <limitvalue>
 *     <matchcheckedonly> <match_operator>                                   (smart playlists)
 *   R <name> <field> <action> <string> <fromvalue> <fromdate> <fromunits> <tovalue> <todate> <tounits>
 *
 * dump-photos output: "I <id> <has thumbnail 0|1>" per photo, "A <name> <album_type> <member ids ,>"
 *
 * Build: gcc -o libgpod_tool libgpod_tool.c $(pkg-config --cflags --libs libgpod-1.0 glib-2.0)
 */
#include <gpod/itdb.h>
#include <glib.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *s(const char *v) { return v ? v : ""; }

static int build(const char *mp, const char *spec_path) {
    GError *err = NULL;
    gchar *spec = NULL;
    if (!g_file_get_contents(spec_path, &spec, NULL, &err)) {
        fprintf(stderr, "spec: %s\n", err->message);
        return 2;
    }
    Itdb_iTunesDB *itdb = itdb_new();
    itdb_set_mountpoint(itdb, mp);
    Itdb_Playlist *mpl = itdb_playlist_new("iPod", FALSE);
    itdb_playlist_set_mpl(mpl);
    itdb_playlist_add(itdb, mpl, -1);
    GPtrArray *tracks = g_ptr_array_new();

    gchar **lines = g_strsplit(spec, "\n", -1);
    for (gchar **l = lines; *l; l++) {
        if (!**l) continue;
        gchar **f = g_strsplit(*l, "\t", -1);
        guint n = g_strv_length(f);
        if (!strcmp(f[0], "name") && n >= 2) {
            g_free(mpl->name);
            mpl->name = g_strdup(f[1]);
        } else if (!strcmp(f[0], "track") && n >= 11) {
            Itdb_Track *t = itdb_track_new();
            t->title = g_strdup(f[2]);
            t->artist = g_strdup(f[3]);
            t->album = g_strdup(f[4]);
            t->genre = g_strdup(f[5]);
            t->filetype = g_strdup("MPEG audio file");
            t->track_nr = atoi(f[6]);
            t->year = atoi(f[7]);
            t->mediatype = (guint32)strtoul(f[8], NULL, 0);
            t->rating = atoi(f[9]);
            t->playcount = atoi(f[10]);
            t->tracklen = 1000;
            t->bitrate = 128;
            t->samplerate = 44100;
            if (t->mediatype & (ITDB_MEDIATYPE_PODCAST | ITDB_MEDIATYPE_AUDIOBOOK)) {
                t->remember_playback_position = 1;
                t->skip_when_shuffling = 1;
            }
            FILE *fh = fopen(f[1], "rb");
            if (fh) {
                fseek(fh, 0, SEEK_END);
                t->size = (guint32)ftell(fh);
                fclose(fh);
            }
            itdb_track_add(itdb, t, -1);
            itdb_playlist_add_track(mpl, t, -1);
            if (!itdb_cp_track_to_ipod(t, f[1], &err)) {
                fprintf(stderr, "copy %s: %s\n", f[1], err ? err->message : "?");
                return 3;
            }
            g_ptr_array_add(tracks, t);
        } else if (!strcmp(f[0], "playlist") && n >= 4) {
            Itdb_Playlist *pl = itdb_playlist_new(f[1], FALSE);
            if (atoi(f[2])) itdb_playlist_set_podcasts(pl);
            itdb_playlist_add(itdb, pl, -1);
            gchar **idx = g_strsplit(f[3], ",", -1);
            for (gchar **i = idx; *i; i++) {
                if (!**i) continue;
                guint k = (guint)atoi(*i);
                if (k < tracks->len) itdb_playlist_add_track(pl, g_ptr_array_index(tracks, k), -1);
            }
            g_strfreev(idx);
        }
        g_strfreev(f);
    }
    g_strfreev(lines);
    if (!itdb_write(itdb, &err)) {
        fprintf(stderr, "write: %s\n", err ? err->message : "?");
        return 4;
    }
    itdb_free(itdb);
    g_ptr_array_free(tracks, TRUE);
    g_free(spec);
    return 0;
}

static int dump(const char *mp, gboolean update_spl) {
    GError *err = NULL;
    Itdb_iTunesDB *itdb = itdb_parse(mp, &err);
    if (!itdb) {
        fprintf(stderr, "parse: %s\n", err ? err->message : "?");
        return 5;
    }
    if (update_spl) itdb_spl_update_all(itdb);
    for (GList *g = itdb->tracks; g; g = g->next) {
        Itdb_Track *t = g->data;
        printf("T\t%u\t%llu\t%s\t%s\t%s\t%s\t%d\t%d\t%u\t%u\t%u\t%s\t%u\t%s\t%u\t%u\n", t->id,
               (unsigned long long)t->dbid, s(t->title), s(t->artist), s(t->album), s(t->genre),
               t->track_nr, t->year, t->mediatype, t->rating, t->playcount, s(t->ipod_path),
               t->compilation, s(t->albumartist), t->bookmark_time, t->remember_playback_position);
    }
    for (GList *g = itdb->playlists; g; g = g->next) {
        Itdb_Playlist *pl = g->data;
        printf("P\t%s\t%d\t%d\t%u\t", s(pl->name), itdb_playlist_is_mpl(pl) ? 1 : 0,
               itdb_playlist_is_podcasts(pl) ? 1 : 0, g_list_length(pl->members));
        for (GList *m = pl->members; m; m = m->next) {
            Itdb_Track *t = m->data;
            printf("%s%s", m == pl->members ? "" : "|", s(t->title));
        }
        printf("\n");
        if (!pl->is_spl) continue;
        printf("S\t%s\t%d\t%d\t%d\t%u\t%u\t%u\t%d\t%u\n", s(pl->name), pl->splpref.liveupdate,
               pl->splpref.checkrules, pl->splpref.checklimits, pl->splpref.limittype,
               pl->splpref.limitsort, pl->splpref.limitvalue, pl->splpref.matchcheckedonly,
               pl->splrules.match_operator);
        for (GList *r = pl->splrules.rules; r; r = r->next) {
            Itdb_SPLRule *rule = r->data;
            printf("R\t%s\t%u\t%u\t%s\t%lld\t%lld\t%llu\t%lld\t%lld\t%llu\n", s(pl->name),
                   rule->field, rule->action, s(rule->string), (long long)rule->fromvalue,
                   (long long)rule->fromdate, (unsigned long long)rule->fromunits,
                   (long long)rule->tovalue, (long long)rule->todate,
                   (unsigned long long)rule->tounits);
        }
    }
    itdb_free(itdb);
    return 0;
}

static int dump_photos(const char *mp) {
    GError *err = NULL;
    Itdb_PhotoDB *db = itdb_photodb_parse(mp, &err);
    if (!db) {
        fprintf(stderr, "photodb: %s\n", err ? err->message : "parse failed");
        return 2;
    }
    for (GList *l = db->photos; l; l = l->next) {
        Itdb_Artwork *a = l->data;
        printf("I\t%u\t%d\n", a->id, a->thumbnail != NULL);
    }
    for (GList *l = db->photoalbums; l; l = l->next) {
        Itdb_PhotoAlbum *al = l->data;
        printf("A\t%s\t%u\t", s(al->name), al->album_type);
        for (GList *m = al->members; m; m = m->next)
            printf("%s%u", m == al->members ? "" : ",", ((Itdb_Artwork *)m->data)->id);
        printf("\n");
    }
    itdb_photodb_free(db);
    return 0;
}

int main(int argc, char **argv) {
    if (argc >= 4 && !strcmp(argv[1], "build")) return build(argv[2], argv[3]);
    if (argc >= 3 && !strcmp(argv[1], "dump")) return dump(argv[2], FALSE);
    if (argc >= 3 && !strcmp(argv[1], "dump-spl")) return dump(argv[2], TRUE);
    if (argc >= 3 && !strcmp(argv[1], "dump-photos")) return dump_photos(argv[2]);
    fprintf(stderr, "usage: %s build <mountpoint> <spec.tsv> | dump <mountpoint>\n", argv[0]);
    return 1;
}
