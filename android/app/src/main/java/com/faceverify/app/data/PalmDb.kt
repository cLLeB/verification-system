package com.faceverify.app.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

/** Palm templates in their OWN encrypted SQLite file (`palmverify.db`), fully
 *  isolated from face (`faceverify.db`). Reuses the same Person/Embedding entities
 *  and DAO — Room keeps the tables separate because the database files differ — so
 *  palm and face data are never mixed and never cross-matched. */
@Database(entities = [Person::class, Embedding::class], version = 1, exportSchema = false)
abstract class PalmDb : RoomDatabase() {
    abstract fun dao(): FaceDao

    companion object {
        @Volatile private var INSTANCE: PalmDb? = null

        fun get(context: Context): PalmDb = INSTANCE ?: synchronized(this) {
            INSTANCE ?: Room.databaseBuilder(
                context.applicationContext, PalmDb::class.java, "palmverify.db"
            ).build().also { INSTANCE = it }
        }
    }
}
