/*
 * libgpod reference tool for pygpod's interop tests (tests/test_libgpod_interop.py).
 *
 *   libgpod_tool build <mountpoint> <spec.tsv>   write an iTunesDB with libgpod
 *   libgpod_tool dump  <mountpoint>              print the iTunesDB as libgpod parses it
 *   libgpod_tool dump-spl <mountpoint>           same, after itdb_spl_update_all() (libgpod's members)
 *   libgpod_tool dump-photos <mountpoint>        print the Photo Database as libgpod parses it
 *   libgpod_tool shuffle-sd <mountpoint> <out> parse the iTunesDB, write libgpod's iTunesSD to <out>
 *   libgpod_tool dump-video <mountpoint>         print video-related (and other extra) track fields
 *   libgpod_tool dump-chapters <mountpoint>      print the chapters of every track
 *   libgpod_tool dump-tags <mountpoint>          print sort, rating, BPM, advisory, podcast fields
 *   libgpod_tool dump-photos-full <mountpoint>   Photo Database incl. thumbnails as libgpod sees them
 *   libgpod_tool build-photos <mountpoint> <photos.tsv>  write a Photo Database with libgpod
 *   libgpod_tool dump-artwork <mountpoint>       cover art thumbnails of every track as libgpod sees them
 *   libgpod_tool dump-times <mountpoint>         track and playlist timestamps as libgpod converts them
 *
 * (tests/test_libgpod_interop.py, tests/test_photodb_libgpod.py and others use it)
 *
 * spec.tsv (tab separated, one record per line):
 *   name     <master playlist name>
 *   track    <file> <title> <artist> <album> <genre> <track_nr> <year> <mediatype> <rating> <playcount>
 *   playlist <name> <podcast 0|1> <comma separated 0-based track indexes>
 *   chapters <0-based track index> <start_ms>:<title>|<start_ms>:<title>...  (itdb_chapterdata)
 *   podcast  <0-based track index> <podcasturl> <podcastrss>
 *   sort     <0-based track index> <composer> <cd_nr> <sort_title> <sort_artist> <sort_album>
 *            <sort_composer> <albumartist>   (empty strings stay empty, i.e. not set)
 *   artwork  <0-based track index> <image file>      (itdb_track_set_thumbnails)
 *   times    <0-based track index> <time_added> <time_modified> <time_played> <time_released>
 *            (time_t, converted to the iPod's time by libgpod when writing)
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
 * dump-video output: V <title> <mediatype> <movie_flag> <tvshow> <tvepisode> <tvnetwork> <season_nr>
 *   <episode_nr> <filetype_marker hex> <filetype> <tracklen> <size> <bitrate> <samplerate> <year>
 *   <has_artwork> <has thumbnails 0|1> <soundcheck> <pregap> <samplecount> <postgap> <gapless_data>
 *   <gapless_track_flag> <gapless_album_flag>
 *
 * dump-chapters output: C <title> <n_chapters> <start_ms>:<chapter title> joined by '|'
 *
 * dump-tags output: G <title> <compilation> <sort_artist> <sort_title> <sort_album>
 *   <sort_albumartist> <sort_composer> <sort_tvshow> <rating> <BPM> <grouping> <explicit_flag>
 *   <description> <podcasturl> <podcastrss> <time_released (unix)> <category> <keywords>
 *
 * dump-photos-full output (tab separated):
 *   D <ipod generation (Itdb_IpodGeneration)> <supports photo 0|1>
 *   I <id> <thumb type (4 = ipod)> <n thumbnail items> <n items libgpod decodes to a pixbuf>
 *     <dbid> <artwork_size> <creation_date> <digitized_date> <rating>
 *   F <photo id> <format id (-1: unknown to libgpod)> <width> <height> <size> <ithmb offset>
 *     <filename as stored, e.g. ":Thumbs:F1024_1.ithmb"> <horizontal_padding> <vertical_padding>
 *   A <name> <album_type> <n members> <member ids joined by ','>
 *
 * dump-artwork output (tab separated):
 *   K <title> <n thumbnail items> <n items libgpod decodes to a pixbuf>
 *   F <title> <format id> <width> <height> <size> <horizontal_padding> <vertical_padding>
 *   P <title> <width>x<height> <MD5 of the decoded pixels>   (per decoded thumbnail)
 *
 * dump-times output (tab separated, time_t as libgpod converts the iPod's timestamps):
 *   W <title> <time_added> <time_modified> <time_played> <time_released> <time_skipped>
 *   Y <playlist name> <timecreated>
 *
 * photos.tsv (tab separated, one record per line, applied in order):
 *   photo  <image file>                                   itdb_photodb_add_photo (Photo Library)
 *   album  <name> <comma separated 0-based photo indexes> itdb_photodb_photoalbum_create + add_photo
 *   remove <0-based photo index>                          itdb_photodb_remove_photo (all albums)
 *
 * Build: gcc -o libgpod_tool libgpod_tool.c $(pkg-config --cflags --libs libgpod-1.0 glib-2.0)
 */
#include <gpod/itdb.h>
#include <gdk-pixbuf/gdk-pixbuf.h>
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
        } else if (!strcmp(f[0], "podcast") && n >= 4) {
            guint k = (guint)atoi(f[1]);
            if (k < tracks->len) {
                Itdb_Track *t = g_ptr_array_index(tracks, k);
                g_free(t->podcasturl);
                g_free(t->podcastrss);
                t->podcasturl = g_strdup(f[2]);
                t->podcastrss = g_strdup(f[3]);
            }
        } else if (!strcmp(f[0], "sort") && n >= 9) {
            guint k = (guint)atoi(f[1]);
            if (k < tracks->len) {
                Itdb_Track *t = g_ptr_array_index(tracks, k);
                g_free(t->composer);
                t->composer = g_strdup(f[2]);
                t->cd_nr = atoi(f[3]);
                g_free(t->sort_title);
                t->sort_title = g_strdup(f[4]);
                g_free(t->sort_artist);
                t->sort_artist = g_strdup(f[5]);
                g_free(t->sort_album);
                t->sort_album = g_strdup(f[6]);
                g_free(t->sort_composer);
                t->sort_composer = g_strdup(f[7]);
                g_free(t->albumartist);
                t->albumartist = *f[8] ? g_strdup(f[8]) : NULL;
            }
        } else if (!strcmp(f[0], "artwork") && n >= 3) {
            guint k = (guint)atoi(f[1]);
            if (k < tracks->len && !itdb_track_set_thumbnails(g_ptr_array_index(tracks, k), f[2])) {
                fprintf(stderr, "artwork %s failed\n", f[2]);
                return 3;
            }
        } else if (!strcmp(f[0], "times") && n >= 6) {
            guint k = (guint)atoi(f[1]);
            if (k < tracks->len) {
                Itdb_Track *t = g_ptr_array_index(tracks, k);
                t->time_added = (time_t)strtoll(f[2], NULL, 10);
                t->time_modified = (time_t)strtoll(f[3], NULL, 10);
                t->time_played = (time_t)strtoll(f[4], NULL, 10);
                t->time_released = (time_t)strtoll(f[5], NULL, 10);
            }
        } else if (!strcmp(f[0], "chapters") && n >= 3) {
            guint k = (guint)atoi(f[1]);
            if (k < tracks->len) {
                Itdb_Track *t = g_ptr_array_index(tracks, k);
                if (!t->chapterdata) t->chapterdata = itdb_chapterdata_new();
                gchar **chaps = g_strsplit(f[2], "|", -1);
                for (gchar **c = chaps; *c; c++) {
                    gchar *colon = strchr(*c, ':');
                    if (!colon) continue;
                    *colon = '\0';
                    itdb_chapterdata_add_chapter(t->chapterdata, (guint32)strtoul(*c, NULL, 10),
                                                 colon + 1);
                }
                g_strfreev(chaps);
            }
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

static int dump_video(const char *mp) {
    GError *err = NULL;
    Itdb_iTunesDB *itdb = itdb_parse(mp, &err);
    if (!itdb) {
        fprintf(stderr, "parse: %s\n", err ? err->message : "?");
        return 5;
    }
    for (GList *g = itdb->tracks; g; g = g->next) {
        Itdb_Track *t = g->data;
        printf("V\t%s\t%u\t%u\t%s\t%s\t%s\t%u\t%u\t%08x\t%s\t%d\t%u\t%d\t%u\t%d\t%u\t%d\t%u\t%u\t%llu\t%u\t%u\t%u\t%u\n",
               s(t->title), t->mediatype, t->movie_flag, s(t->tvshow), s(t->tvepisode),
               s(t->tvnetwork), t->season_nr, t->episode_nr, t->filetype_marker, s(t->filetype),
               t->tracklen, t->size, t->bitrate, t->samplerate, t->year, t->has_artwork,
               itdb_track_has_thumbnails(t) ? 1 : 0, t->soundcheck, t->pregap,
               (unsigned long long)t->samplecount, t->postgap, t->gapless_data,
               t->gapless_track_flag, t->gapless_album_flag);
    }
    itdb_free(itdb);
    return 0;
}

static int dump_chapters(const char *mp) {
    GError *err = NULL;
    Itdb_iTunesDB *itdb = itdb_parse(mp, &err);
    if (!itdb) {
        fprintf(stderr, "parse: %s\n", err ? err->message : "?");
        return 5;
    }
    for (GList *g = itdb->tracks; g; g = g->next) {
        Itdb_Track *t = g->data;
        GList *chaps = t->chapterdata ? t->chapterdata->chapters : NULL;
        printf("C\t%s\t%u\t", s(t->title), g_list_length(chaps));
        for (GList *c = chaps; c; c = c->next) {
            Itdb_Chapter *ch = c->data;
            printf("%s%u:%s", c == chaps ? "" : "|", ch->startpos, s(ch->chaptertitle));
        }
        printf("\n");
    }
    itdb_free(itdb);
    return 0;
}

static int dump_tags(const char *mp) {
    GError *err = NULL;
    Itdb_iTunesDB *itdb = itdb_parse(mp, &err);
    if (!itdb) {
        fprintf(stderr, "parse: %s\n", err ? err->message : "?");
        return 5;
    }
    for (GList *g = itdb->tracks; g; g = g->next) {
        Itdb_Track *t = g->data;
        printf("G\t%s\t%u\t%s\t%s\t%s\t%s\t%s\t%s\t%u\t%u\t%s\t%u\t%s\t%s\t%s\t%lld\t%s\t%s\n",
               s(t->title), t->compilation, s(t->sort_artist), s(t->sort_title), s(t->sort_album),
               s(t->sort_albumartist), s(t->sort_composer), s(t->sort_tvshow), t->rating, t->BPM,
               s(t->grouping), t->explicit_flag, s(t->description), s(t->podcasturl),
               s(t->podcastrss), (long long)t->time_released, s(t->category), s(t->keywords));
    }
    itdb_free(itdb);
    return 0;
}

static int dump_times(const char *mp) {
    GError *err = NULL;
    Itdb_iTunesDB *itdb = itdb_parse(mp, &err);
    if (!itdb) {
        fprintf(stderr, "parse: %s\n", err ? err->message : "?");
        return 5;
    }
    for (GList *g = itdb->tracks; g; g = g->next) {
        Itdb_Track *t = g->data;
        printf("W\t%s\t%lld\t%lld\t%lld\t%lld\t%lld\n", s(t->title), (long long)t->time_added,
               (long long)t->time_modified, (long long)t->time_played,
               (long long)t->time_released, (long long)t->last_skipped);
    }
    for (GList *g = itdb->playlists; g; g = g->next) {
        Itdb_Playlist *pl = g->data;
        printf("Y\t%s\t%lld\n", s(pl->name), (long long)pl->timestamp);
    }
    itdb_free(itdb);
    return 0;
}

static int shuffle_sd(const char *mp, const char *out) {
    GError *err = NULL;
    Itdb_iTunesDB *itdb = itdb_parse(mp, &err);
    if (!itdb) {
        fprintf(stderr, "parse: %s\n", err ? err->message : "?");
        return 5;
    }
    if (!itdb_shuffle_write_file(itdb, out, &err)) {
        fprintf(stderr, "shuffle write: %s\n", err ? err->message : "?");
        return 4;
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

/* libgpod 0.8.3 internal thumbnail structs (itdb_thumb.h / itdb_device.h are not installed). Only
 * the leading members read here are declared; their layout is libgpod's. */
typedef struct {
    gint format_id;
    gint width;
    gint height;
} Tool_ArtworkFormat;
typedef struct {
    const Tool_ArtworkFormat *format;
    gchar *filename;
    guint32 offset;
    guint32 size;
    gint16 width;
    gint16 height;
    gint16 horizontal_padding;
    gint16 vertical_padding;
} Tool_ThumbIpodItem;
typedef struct {
    gint data_type; /* ItdbThumbDataType: 4 = ITDB_THUMB_TYPE_IPOD */
    gint rotation;
} Tool_Thumb;
typedef struct {
    Tool_Thumb parent;
    GList *thumbs;
} Tool_ThumbIpod;
#define TOOL_THUMB_TYPE_IPOD 4

static int dump_photos_full(const char *mp) {
    GError *err = NULL;
    Itdb_PhotoDB *db = itdb_photodb_parse(mp, &err);
    if (!db) {
        fprintf(stderr, "photodb: %s\n", err ? err->message : "parse failed");
        return 2;
    }
    const Itdb_IpodInfo *info = db->device ? itdb_device_get_ipod_info(db->device) : NULL;
    printf("D\t%d\t%d\n", info ? (int)info->ipod_generation : -1,
           db->device && itdb_device_supports_photo(db->device) ? 1 : 0);
    for (GList *l = db->photos; l; l = l->next) {
        Itdb_Artwork *a = l->data;
        Tool_Thumb *th = (Tool_Thumb *)a->thumbnail;
        guint n_items = 0, n_decoded = 0;
        if (th && th->data_type == TOOL_THUMB_TYPE_IPOD) {
            n_items = g_list_length(((Tool_ThumbIpod *)th)->thumbs);
            GList *pixbufs = itdb_thumb_to_pixbufs(db->device, a->thumbnail);
            for (GList *p = pixbufs; p; p = p->next)
                if (p->data) n_decoded++;
            g_list_free(pixbufs); /* the pixbufs leak: the tool exits right after */
        }
        printf("I\t%u\t%d\t%u\t%u\t%llu\t%u\t%lld\t%lld\t%u\n", a->id, th ? th->data_type : 0,
               n_items, n_decoded, (unsigned long long)a->dbid, a->artwork_size,
               (long long)a->creation_date, (long long)a->digitized_date, a->rating);
        if (!th || th->data_type != TOOL_THUMB_TYPE_IPOD) continue;
        for (GList *t = ((Tool_ThumbIpod *)th)->thumbs; t; t = t->next) {
            Tool_ThumbIpodItem *it = t->data;
            printf("F\t%u\t%d\t%d\t%d\t%u\t%u\t%s\t%d\t%d\n", a->id,
                   it->format ? it->format->format_id : -1, it->width, it->height, it->size,
                   it->offset, s(it->filename), it->horizontal_padding, it->vertical_padding);
        }
    }
    for (GList *l = db->photoalbums; l; l = l->next) {
        Itdb_PhotoAlbum *al = l->data;
        printf("A\t%s\t%u\t%u\t", s(al->name), al->album_type, g_list_length(al->members));
        for (GList *m = al->members; m; m = m->next)
            printf("%s%u", m == al->members ? "" : ",", ((Itdb_Artwork *)m->data)->id);
        printf("\n");
    }
    itdb_photodb_free(db);
    return 0;
}

static int dump_artwork(const char *mp) {
    GError *err = NULL;
    Itdb_iTunesDB *itdb = itdb_parse(mp, &err);
    if (!itdb) {
        fprintf(stderr, "parse: %s\n", err ? err->message : "?");
        return 5;
    }
    for (GList *g = itdb->tracks; g; g = g->next) {
        Itdb_Track *t = g->data;
        Tool_Thumb *th = t->artwork ? (Tool_Thumb *)t->artwork->thumbnail : NULL;
        if (!th || th->data_type != TOOL_THUMB_TYPE_IPOD) continue;
        guint n_decoded = 0;
        GList *pixbufs = itdb_thumb_to_pixbufs(itdb->device, t->artwork->thumbnail);
        for (GList *p = pixbufs; p; p = p->next)
            if (p->data) n_decoded++;
        GList *items = ((Tool_ThumbIpod *)th)->thumbs;
        printf("K\t%s\t%u\t%u\n", s(t->title), g_list_length(items), n_decoded);
        for (GList *i = items; i; i = i->next) {
            Tool_ThumbIpodItem *it = i->data;
            printf("F\t%s\t%d\t%d\t%d\t%u\t%d\t%d\n", s(t->title),
                   it->format ? it->format->format_id : -1, it->width, it->height, it->size,
                   it->horizontal_padding, it->vertical_padding);
        }
        for (GList *p = pixbufs; p; p = p->next) {
            GdkPixbuf *pb = p->data;
            if (!pb) continue;
            gchar *md5 = g_compute_checksum_for_data(
                G_CHECKSUM_MD5, gdk_pixbuf_read_pixels(pb), gdk_pixbuf_get_byte_length(pb));
            printf("P\t%s\t%dx%d\t%s\n", s(t->title), gdk_pixbuf_get_width(pb),
                   gdk_pixbuf_get_height(pb), md5);
            g_free(md5);
        }
        g_list_free(pixbufs); /* the pixbufs leak: the tool exits right after */
    }
    itdb_free(itdb);
    return 0;
}

static int build_photos(const char *mp, const char *spec_path) {
    GError *err = NULL;
    gchar *spec = NULL;
    if (!g_file_get_contents(spec_path, &spec, NULL, &err)) {
        fprintf(stderr, "spec: %s\n", err->message);
        return 2;
    }
    Itdb_PhotoDB *db = itdb_photodb_create(mp);
    if (!db) {
        fprintf(stderr, "photodb create failed\n");
        return 2;
    }
    GPtrArray *photos = g_ptr_array_new();
    gchar **lines = g_strsplit(spec, "\n", -1);
    for (gchar **l = lines; *l; l++) {
        if (!**l) continue;
        gchar **f = g_strsplit(*l, "\t", -1);
        guint n = g_strv_length(f);
        if (!strcmp(f[0], "photo") && n >= 2) {
            Itdb_Artwork *a = itdb_photodb_add_photo(db, f[1], -1, 0, &err);
            if (!a) {
                fprintf(stderr, "add %s: %s\n", f[1], err ? err->message : "?");
                return 3;
            }
            g_ptr_array_add(photos, a);
        } else if (!strcmp(f[0], "album") && n >= 3) {
            Itdb_PhotoAlbum *al = itdb_photodb_photoalbum_create(db, f[1], -1);
            gchar **idx = g_strsplit(f[2], ",", -1);
            for (gchar **i = idx; *i; i++) {
                if (!**i) continue;
                guint k = (guint)atoi(*i);
                if (k < photos->len && g_ptr_array_index(photos, k))
                    itdb_photodb_photoalbum_add_photo(db, al, g_ptr_array_index(photos, k), -1);
            }
            g_strfreev(idx);
        } else if (!strcmp(f[0], "remove") && n >= 2) {
            guint k = (guint)atoi(f[1]);
            if (k < photos->len && g_ptr_array_index(photos, k)) {
                itdb_photodb_remove_photo(db, NULL, g_ptr_array_index(photos, k));
                g_ptr_array_index(photos, k) = NULL;
            }
        }
        g_strfreev(f);
    }
    g_strfreev(lines);
    if (!itdb_photodb_write(db, &err)) {
        fprintf(stderr, "write: %s\n", err ? err->message : "?");
        return 4;
    }
    itdb_photodb_free(db);
    g_ptr_array_free(photos, TRUE);
    g_free(spec);
    return 0;
}

int main(int argc, char **argv) {
    if (argc >= 4 && !strcmp(argv[1], "build")) return build(argv[2], argv[3]);
    if (argc >= 3 && !strcmp(argv[1], "dump")) return dump(argv[2], FALSE);
    if (argc >= 3 && !strcmp(argv[1], "dump-spl")) return dump(argv[2], TRUE);
    if (argc >= 3 && !strcmp(argv[1], "dump-photos")) return dump_photos(argv[2]);
    if (argc >= 3 && !strcmp(argv[1], "dump-video")) return dump_video(argv[2]);
    if (argc >= 3 && !strcmp(argv[1], "dump-chapters")) return dump_chapters(argv[2]);
    if (argc >= 3 && !strcmp(argv[1], "dump-tags")) return dump_tags(argv[2]);
    if (argc >= 3 && !strcmp(argv[1], "dump-photos-full")) return dump_photos_full(argv[2]);
    if (argc >= 3 && !strcmp(argv[1], "dump-artwork")) return dump_artwork(argv[2]);
    if (argc >= 3 && !strcmp(argv[1], "dump-times")) return dump_times(argv[2]);
    if (argc >= 4 && !strcmp(argv[1], "build-photos")) return build_photos(argv[2], argv[3]);
    if (argc >= 4 && !strcmp(argv[1], "shuffle-sd")) return shuffle_sd(argv[2], argv[3]);
    fprintf(stderr, "usage: %s build <mountpoint> <spec.tsv> | dump <mountpoint>\n", argv[0]);
    return 1;
}
