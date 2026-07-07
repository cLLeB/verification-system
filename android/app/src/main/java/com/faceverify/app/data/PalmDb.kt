package com.faceverify.app.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

/** Palm templates in their OWN encrypted SQLite file (`palmverify.db`), fully
 *  isolated from face (`faceverify.db`). Reuses the same Person/Embedding entities
 *  and DAO — Room keeps the tables separate because the database files differ — so
 *  palm and face data are never mixed and never cross-matched. */
@Database(entities = [Person::class, Embedding::class], version = 2, exportSchema = false)
abstract class PalmDb : RoomDatabase() {
    abstract fun dao(): FaceDao

    companion object {
        @Volatile private var INSTANCE: PalmDb? = null

        // v1 -> v2: per-person protection-domain seed (null = raw local enrolment).
        private val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE person ADD COLUMN seedBlob BLOB")
            }
        }

        fun get(context: Context): PalmDb = INSTANCE ?: synchronized(this) {
            INSTANCE ?: Room.databaseBuilder(
                context.applicationContext, PalmDb::class.java, "palmverify.db"
            ).addMigrations(MIGRATION_1_2).build().also { INSTANCE = it }
        }
    }
}
