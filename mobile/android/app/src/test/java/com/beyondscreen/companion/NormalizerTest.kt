package com.beyondscreen.companion
import org.junit.Assert.*
import org.junit.Test
class NormalizerTest{@Test fun durationsAreSafe(){assertEquals(2,Normalizer.minutes(120000));assertEquals(0,Normalizer.minutes(-1))}@Test fun retryKeepsReportId(){assertEquals("same",Normalizer.reportId("same"))}@Test fun invalidMinutesRejected(){assertFalse(Normalizer.valid(MobileReport(totalMinutes=-1,apps=emptyList())))}}
